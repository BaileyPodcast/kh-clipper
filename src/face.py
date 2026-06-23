"""
KH Clipper — face analysis for hero-focus reframing (Stage 4 helper).

The YouTube Shorts strategy is solo guest, host off-screen. A centre-crop is right
for a centred solo guest but wrong for a two-person interview (a split-screen kills
the intimacy). This module looks at a cut clip and answers two questions:

  1. Is there one subject, or two-plus? (single vs interview)
  2. Where is the guest horizontally, so reframe can keep them in the 9:16 frame?

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
SAMPLE_FPS = 2.0          # frames per second to sample
MAX_FRAMES = 40           # cap work on long clips
MULTI_RATIO = 0.40        # >= this fraction of frames with 2+ faces = "interview"
CODOMINANT = 0.60         # second face >= 60% the size of the largest = can't tell guest

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


def analyze(clip_path):
    """Return a framing decision dict:
        {width, height, n_frames, multi_ratio, guest_cx, mode, track}
    where mode is 'single' | 'follow' | 'ambiguous', and guest_cx is the horizontal
    pixel centre to crop around (None if we should centre-crop). `track` is a list of
    (t_seconds, cx) samples of the followed face over the clip so reframe can pan with
    the speaker instead of locking a single static window (None when we centre-crop).
    Raises if detection cannot run at all (caller falls back to centre-crop + review)."""
    import mediapipe as mp
    detector = _get_detector()

    with tempfile.TemporaryDirectory() as tmp:
        frames = _sample_frames(clip_path, tmp)
        if not frames:
            raise RuntimeError("no frames sampled (ffmpeg?)")

        width = height = 0
        per_frame = []           # list of [(cx, area), ...] per frame, largest first
        for fp in frames:
            image = mp.Image.create_from_file(fp)
            width, height = image.width, image.height
            res = detector.detect(image)
            faces = []
            for d in res.detections:
                b = d.bounding_box
                faces.append((b.origin_x + b.width / 2.0, float(b.width * b.height)))
            faces.sort(key=lambda f: f[1], reverse=True)   # largest first
            per_frame.append(faces)

    detected = [f for f in per_frame if f]
    n = len(detected)
    if n == 0:
        raise RuntimeError("no faces detected in any sampled frame")

    multi = sum(1 for f in detected if len(f) >= 2)
    multi_ratio = multi / n
    largest_cx = sorted(f[0][0] for f in detected)
    guest_cx = largest_cx[len(largest_cx) // 2]            # median centre of largest face

    if multi_ratio >= MULTI_RATIO:
        # Two-plus people on screen a lot. Can we tell the guest from the host?
        ratios = [f[1][1] / f[0][1] for f in detected if len(f) >= 2 and f[0][1] > 0]
        ratios.sort()
        codominant = ratios and ratios[len(ratios) // 2] >= CODOMINANT
        if codominant:
            mode = "ambiguous"          # two comparable faces — don't guess, flag review
            guest_cx = None
        else:
            mode = "follow"             # one clearly dominant face = the guest
    else:
        mode = "single"                 # mostly one face — re-centre on the guest

    # Per-frame horizontal track of the face we follow (largest face each frame), so
    # reframe can pan with the speaker. Frame i was sampled at i/SAMPLE_FPS seconds.
    # Only meaningful when we actually follow a face; ambiguous clips centre-crop.
    track = None
    if mode in ("single", "follow"):
        track = [(i / SAMPLE_FPS, f[0][0]) for i, f in enumerate(per_frame) if f]

    return {
        "width": width, "height": height, "n_frames": n,
        "multi_ratio": round(multi_ratio, 2), "guest_cx": guest_cx, "mode": mode,
        "track": track,
    }
