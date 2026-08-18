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
TILE_SEP = 0.25           # medians this far apart (fraction of width) = side-by-side tiles

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


def _window_flags(windows, n_frames):
    """Per sampled frame, 1 if the frame's timestamp falls inside any (t0, t1) window.
    Windows are clip-relative seconds (same clock as fi / SAMPLE_FPS)."""
    flags = [0] * n_frames
    for (t0, t1) in windows or []:
        lo = max(0, int(t0 * SAMPLE_FPS))
        hi = min(n_frames - 1, int(t1 * SAMPLE_FPS))
        for fi in range(lo, hi + 1):
            flags[fi] = 1
    return flags


def _diarized_pick(tracks, guest_flags, other_flags):
    """Pick the guest FACE by matching lip motion against the diarized transcript:
    the hero is the face whose mouth moves while the transcript says the GUEST is
    speaking, and stays still while the host speaks. Scores each track as
    (motion during guest windows) - (motion during host windows); the winner needs a
    positive score and a clear margin over any other positive scorer. Returns the
    winning track, or None when the diarization doesn't separate the faces (caller
    falls back to the audio-gated pick). Requires _lip_motion to have run."""
    if not guest_flags or not any(guest_flags):
        return None

    def dscore(tr):
        g = sum(m for fi, m in tr["motion"].items()
                if fi < len(guest_flags) and guest_flags[fi])
        o = sum(m for fi, m in tr["motion"].items()
                if other_flags and fi < len(other_flags) and other_flags[fi])
        return g - o

    ranked = sorted(tracks, key=dscore, reverse=True)
    d0 = dscore(ranked[0])
    d1 = dscore(ranked[1]) if len(ranked) > 1 else 0.0
    if d0 <= 0:
        return None                     # nobody's mouth tracks the guest windows
    if d1 > 0 and d0 < SPEAK_MARGIN * d1:
        return None                     # two faces both track the guest — no clear win
    return ranked[0]


def _tile_bounds(guest_frames, other_tracks, width):
    """When the source is a side-by-side composite (a Riverside split screen), the
    guest lives in their own tile and the crop must NEVER cross the seam into the
    other person's tile. We don't detect the seam pixel; we take the midpoint
    between the guest's median cx and each neighbouring track's median cx as a
    conservative tile edge, and only when the faces sit clearly apart (>= TILE_SEP
    of the width — a genuine two-tile layout, not two people sharing one camera).
    Returns (lo, hi) horizontal bounds for the crop in source pixels, or None."""
    if not guest_frames or not other_tracks or not width:
        return None

    def median_cx(frames):
        cxs = sorted(f["cx"] for f in frames.values())
        return cxs[len(cxs) // 2] if cxs else None

    g = median_cx(guest_frames)
    if g is None:
        return None
    lo, hi = 0.0, float(width)
    split = False
    for tr in other_tracks:
        o = median_cx(tr["frames"])
        if o is None or abs(o - g) < TILE_SEP * width:
            continue                    # too close together to be separate tiles
        split = True
        mid = (g + o) / 2.0
        if o < g:
            lo = max(lo, mid)
        else:
            hi = min(hi, mid)
    return (lo, hi) if split and hi > lo else None


def _face_band(frames_dict, height):
    """The guest's vertical extent across the clip: min top / max bottom of their
    face bbox, normalised to frame height (0=top, 1=bottom). Used by caption.py
    (KH-MGX-001 1.3) to keep captions/the hook banner clear of the guest's face —
    one number pair for the whole clip, not per frame. Reframe only ever crops
    horizontally (full source height kept), so this normalised band survives the
    crop+scale to the output frame unchanged."""
    if not frames_dict or not height:
        return None
    tops, bottoms = [], []
    for f in frames_dict.values():
        _, y, _, h = f["bbox"]
        tops.append(y / height)
        bottoms.append((y + h) / height)
    if not tops:
        return None
    return {"top": max(0.0, min(tops)), "bottom": min(1.0, max(bottoms))}


def analyze(clip_path, guest_windows=None, other_windows=None):
    """Return a framing decision dict:
        {width, height, n_frames, multi_ratio, guest_cx, mode, track, people,
         face_band, bounds}
    where mode is 'single' | 'follow' | 'ambiguous', and guest_cx is the horizontal
    pixel centre to crop around (None if we should centre-crop). `track` is a list of
    (t_seconds, cx) samples of the GUEST (the speaking subject) over the clip so reframe
    can pan with them; `people` is the number of stable subjects on screen. `face_band`
    is the guest's normalised vertical extent (KH-MGX-001 1.3), or None when we don't
    know which face is the guest (mode == "ambiguous" never sets it). `bounds` is the
    guest's tile (lo, hi) when the source is a side-by-side composite, else None.

    `guest_windows` / `other_windows` are clip-relative (t0, t1) second intervals of
    when the diarized transcript says the GUEST / anyone else is speaking. When given,
    the guest face is identified by matching lip motion against those windows (the
    hero is the face that moves when the transcript says the hero talks) — far more
    reliable than audio-gated motion alone on a two-shot where both people talk.
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
                "face_band": _face_band(single, height), "bounds": None,
            }

        # Two-plus subjects: the guest is the one SPEAKING. First try the diarized
        # transcript (lip motion matched against the guest's speech windows); fall
        # back to mouth motion gated by the audio being voiced.
        _lip_motion(frames, tracks, width, height)
        voiced = _audio_voiced(clip_path, n_total)

    def score(tr):
        gated = sum(m * voiced[fi] for fi, m in tr["motion"].items())
        return gated if gated > 0 else sum(tr["motion"].values())  # silence: motion only

    def ambiguous():
        return {"width": width, "height": height, "n_frames": n,
                "multi_ratio": round(multi_ratio, 2), "guest_cx": None,
                "mode": "ambiguous", "track": None, "people": len(tracks),
                "face_band": None, "bounds": None}   # unknown guest — no band, no tile

    guest = _diarized_pick(tracks, _window_flags(guest_windows, n_total),
                           _window_flags(other_windows, n_total))
    if guest is None:
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
            guest = ranked[0]                   # one face clearly larger across the clip
        elif s1 > 0 and s0 < SPEAK_MARGIN * s1:
            # Two subjects speak about equally — we can't confidently say which is the
            # guest. Don't guess; the caller shows both and a producer checks (never an
            # arbitrary pick).
            return ambiguous()
        else:
            guest = ranked[0]                   # one subject clearly does the talking

    track = [(fi / SAMPLE_FPS, f["cx"]) for fi, f in sorted(guest["frames"].items())]
    cxs = sorted(c for _, c in track)
    return {
        "width": width, "height": height, "n_frames": n,
        "multi_ratio": round(multi_ratio, 2),
        "guest_cx": cxs[len(cxs) // 2] if cxs else None,
        "mode": "follow", "track": track or None, "people": len(tracks),
        "face_band": _face_band(guest["frames"], height),
        "bounds": _tile_bounds(guest["frames"],
                               [t for t in tracks if t is not guest], width),
    }
