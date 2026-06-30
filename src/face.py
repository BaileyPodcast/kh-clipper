"""
KH Clipper — face analysis for hero-focus reframing (Stage 4 helper).

The YouTube Shorts strategy is solo guest, host off-screen. A centre-crop is right
for a centred solo guest but wrong for a two-person interview (a split-screen kills
the intimacy). This module looks at a cut clip and answers two questions:

  1. How many people are on screen, and where is each one?
  2. Which of them is the guest (the person we keep in the 9:16 frame)?

It tracks each face as a persistent SUBJECT across the clip (so a two-person shot
becomes two stable tracks, not "whichever face is biggest this frame"), then picks
the guest as the subject who is actually SPEAKING — the face whose mouth moves in
time with the audio. The crop then locks to that one person, so the host is never
half-in-frame and we never sit in the gap between two people.

It uses MediaPipe's Python Tasks FaceDetector (BlazeFace short-range) on frames
sampled with ffmpeg. Everything here is best-effort: if MediaPipe, the model, or
ffmpeg is unavailable, `analyze` raises and the caller falls back to a centre-crop
plus a `framing: review` flag. We NEVER ship a bad crop silently.

Model: assets/models/blaze_face_short_range.tflite
  download: https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite
"""
from __future__ import annotations
import os
import subprocess
import tempfile

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(_HERE, "assets", "models", "blaze_face_short_range.tflite")

# Decision thresholds (tunable).
SAMPLE_FPS = 6.0          # frames per second to sample (enough to read lip motion)
MAX_FRAMES = 150          # cap work on long clips (~25s of analysis)
MULTI_RATIO = 0.40        # >= this fraction of frames with 2+ faces = "interview"
CODOMINANT = 0.60         # second face >= 60% the size of the largest = same-size faces
SPEAK_MARGIN = 1.3        # guest must out-speak the runner-up by this factor to be sure
TRACK_GAP = 0.12          # face-to-track match radius, as a fraction of frame width
MIN_TRACK = 0.12          # keep a subject only if seen in >= this fraction of frames

_detector = None          # module-level cache (loading the model is not free)


def detector_available():
    return os.path.exists(MODEL_PATH)


def _get_detector():
    global _detector
    if _detector is None:
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
        if not os.path.exists(MODEL_PATH):
            raise FileNotFoundError(f"face model missing: {MODEL_PATH}")
        base = python.BaseOptions(model_asset_path=MODEL_PATH)
        opts = vision.FaceDetectorOptions(base_options=base)
        _detector = vision.FaceDetector.create_from_options(opts)
    return _detector


def _sample_frames(clip_path, out_dir):
    """Extract frames at SAMPLE_FPS into out_dir; return sorted file paths."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", clip_path, "-vf", f"fps={SAMPLE_FPS}",
         "-frames:v", str(MAX_FRAMES), os.path.join(out_dir, "f%03d.jpg")],
        capture_output=True, text=True, check=False,
    )
    return sorted(
        os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.endswith(".jpg")
    )


def _associate_tracks(per_frame, frame_w):
    """Group per-frame face detections into persistent subjects by horizontal position.
    Greedy nearest-neighbour association (people in a KH interview sit well apart, so a
    simple 1D match on the face centre is plenty). Returns a list of tracks, each a dict
    {frames: {frame_idx: face}} where face is the (cx, bbox, area) record."""
    gap = TRACK_GAP * frame_w
    tracks = []
    for fi, faces in enumerate(per_frame):
        assigned = set()
        for tr in tracks:
            best, best_d = None, gap
            for j, f in enumerate(faces):
                if j in assigned:
                    continue
                d = abs(f["cx"] - tr["last_cx"])
                if d < best_d:
                    best, best_d = j, d
            if best is not None:
                f = faces[best]
                assigned.add(best)
                tr["frames"][fi] = f
                tr["last_cx"] = f["cx"]
        for j, f in enumerate(faces):
            if j in assigned:
                continue
            tracks.append({"frames": {fi: f}, "last_cx": f["cx"]})
    return tracks


def _mouth_patch(jpg_path, bbox, img_w, img_h):
    """Small grayscale crop of a face's mouth region, for frame-to-frame motion. Returns
    a float32 array, or None if the crop is degenerate."""
    from PIL import Image
    import numpy as np
    x, y, w, h = bbox
    mx0 = max(0, x + 0.18 * w)
    mx1 = min(img_w, x + 0.82 * w)
    my0 = max(0, y + 0.55 * h)
    my1 = min(img_h, y + 1.02 * h)
    if mx1 - mx0 < 4 or my1 - my0 < 4:
        return None
    im = Image.open(jpg_path).convert("L").crop((mx0, my0, mx1, my1)).resize((32, 16))
    return np.asarray(im, dtype=np.float32)


def _lip_motion(frames, tracks, img_w, img_h):
    """Per track, per frame, how much the mouth region moved since that track's previous
    frame (a speaking mouth moves; a listening one barely does). Fills tr['motion'][fi]."""
    for tr in tracks:
        prev_fi, prev_patch = None, None
        tr["motion"] = {}
        for fi in sorted(tr["frames"]):
            patch = _mouth_patch(frames[fi], tr["frames"][fi]["bbox"], img_w, img_h)
            if patch is not None and prev_patch is not None and prev_patch.shape == patch.shape:
                import numpy as np
                tr["motion"][fi] = float(np.mean(np.abs(patch - prev_patch)))
            prev_fi, prev_patch = fi, patch


def _audio_voiced(clip_path, n_frames):
    """A 0/1 voiced flag per sampled frame, from the clip's own audio RMS. Returns a list
    of length n_frames (all 1s if audio can't be read, so motion alone decides)."""
    try:
        import numpy as np
        r = subprocess.run(
            ["ffmpeg", "-v", "quiet", "-i", clip_path, "-ac", "1", "-ar", "16000",
             "-f", "f32le", "-"], capture_output=True)
        pcm = np.frombuffer(r.stdout, dtype=np.float32)
        if pcm.size < 1600:
            return [1] * n_frames
        sr = 16000
        out = []
        half = int(sr * 0.12)
        for fi in range(n_frames):
            c = int((fi / SAMPLE_FPS) * sr)
            seg = pcm[max(0, c - half): c + half]
            out.append(float(np.sqrt(np.mean(seg ** 2))) if seg.size else 0.0)
        out = np.asarray(out)
        thr = 0.18 * (np.percentile(out, 95) + 1e-9)
        return [(1 if v >= thr else 0) for v in out]
    except Exception:
        return [1] * n_frames


def analyze(clip_path):
    """Return a framing decision dict:
        {width, height, n_frames, multi_ratio, guest_cx, mode, track, people}
    where mode is 'single' | 'follow' | 'ambiguous', and guest_cx is the horizontal
    pixel centre to crop around (None if we should centre-crop). `track` is a list of
    (t_seconds, cx) samples of the GUEST (the speaking subject) over the clip so reframe
    can pan with them; `people` is the number of stable subjects on screen.
    Raises if detection cannot run at all (caller falls back to centre-crop + review)."""
    import mediapipe as mp
    import numpy as np
    detector = _get_detector()

    with tempfile.TemporaryDirectory() as tmp:
        frames = _sample_frames(clip_path, tmp)
        if not frames:
            raise RuntimeError("no frames sampled (ffmpeg?)")

        width = height = 0
        per_frame = []           # per frame: list of face records (largest first)
        for fp in frames:
            image = mp.Image.create_from_file(fp)
            width, height = image.width, image.height
            res = detector.detect(image)
            faces = []
            for d in res.detections:
                b = d.bounding_box
                faces.append({
                    "cx": b.origin_x + b.width / 2.0,
                    "area": float(b.width * b.height),
                    "bbox": (b.origin_x, b.origin_y, b.width, b.height),
                })
            faces.sort(key=lambda f: f["area"], reverse=True)
            per_frame.append(faces)

        detected = [f for f in per_frame if f]
        n = len(detected)
        if n == 0:
            raise RuntimeError("no faces detected in any sampled frame")

        multi = sum(1 for f in detected if len(f) >= 2)
        multi_ratio = multi / n

        # Persistent subjects across the clip (vs "biggest face this frame").
        tracks = _associate_tracks(per_frame, width)
        n_total = len(per_frame)
        tracks = [t for t in tracks if len(t["frames"]) >= max(3, MIN_TRACK * n_total)]

        # One subject (a solo guest, possibly off-centre): re-centre + pan on them.
        if len(tracks) <= 1:
            single = tracks[0]["frames"] if tracks else \
                {i: f[0] for i, f in enumerate(per_frame) if f}
            track = [(i / SAMPLE_FPS, single[i]["cx"]) for i in sorted(single)]
            cxs = sorted(c for _, c in track)
            return {
                "width": width, "height": height, "n_frames": n,
                "multi_ratio": round(multi_ratio, 2),
                "guest_cx": cxs[len(cxs) // 2] if cxs else None,
                "mode": "single", "track": track or None, "people": max(1, len(tracks)),
            }

        # Two-plus subjects: the guest is the one SPEAKING. Score each subject by mouth
        # motion gated by the audio being voiced, and follow the top scorer.
        _lip_motion(frames, tracks, width, height)
        voiced = _audio_voiced(clip_path, n_total)

    def score(tr):
        gated = sum(m * voiced[fi] for fi, m in tr["motion"].items())
        return gated if gated > 0 else sum(tr["motion"].values())  # silence: motion only

    def ambiguous():
        return {"width": width, "height": height, "n_frames": n,
                "multi_ratio": round(multi_ratio, 2), "guest_cx": None,
                "mode": "ambiguous", "track": None, "people": len(tracks)}

    ranked = sorted(tracks, key=score, reverse=True)
    s0 = score(ranked[0])
    s1 = score(ranked[1]) if len(ranked) > 1 else 0.0

    if s0 <= 0:
        # No lip/audio signal at all (e.g. stills, music-only). Fall back to the size
        # heuristic: if the two faces are the same size we can't guess -> review.
        ratios = [f[1]["area"] / f[0]["area"] for f in detected if len(f) >= 2 and f[0]["area"] > 0]
        ratios.sort()
        if ratios and ratios[len(ratios) // 2] >= CODOMINANT:
            return ambiguous()
        guest = ranked[0]                       # one face clearly larger across the clip
    elif s1 > 0 and s0 < SPEAK_MARGIN * s1:
        # Two subjects speak about equally — we can't confidently say which is the guest.
        # Don't guess; centre-crop and let a producer check (never an arbitrary pick).
        return ambiguous()
    else:
        guest = ranked[0]                       # one subject clearly does the talking

    track = [(fi / SAMPLE_FPS, f["cx"]) for fi, f in sorted(guest["frames"].items())]
    cxs = sorted(c for _, c in track)
    return {
        "width": width, "height": height, "n_frames": n,
        "multi_ratio": round(multi_ratio, 2),
        "guest_cx": cxs[len(cxs) // 2] if cxs else None,
        "mode": "follow", "track": track or None, "people": len(tracks),
    }
