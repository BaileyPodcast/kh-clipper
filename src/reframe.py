"""
KH Clipper — Stage 4: reframe (16:9 -> 9:16) with hero-focus speaker-follow.

The Shorts strategy is solo guest, host off-screen. For a centred solo guest a clean
centre-crop is plenty. For a two-person interview a centre-crop can split the frame
between host and guest, which kills the intimacy. So:

  - We sample faces in the clip (src/face.py, MediaPipe BlazeFace).
  - One subject  -> re-centre the 9:16 crop on the guest (handles off-centre solos too).
  - One dominant face in a multi-person clip -> follow that face (the guest).
  - Two comparable faces we can't tell apart, OR face detection unavailable on a
    diarized interview -> keep the v1 centre-crop and flag the clip `framing: review`
    so a producer checks it. We never ship a bad crop silently.

`reframe()` keeps its original signature (src, out) plus optional `guest`/`use_face`,
and returns a framing flag string: "ok" or "review".

    python -m src.reframe input.mp4 output_vertical.mp4 [--no-face]
"""
from __future__ import annotations
import argparse
import subprocess

W, H = 1080, 1920


def _run_ff(vf, src, out):
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-vf", vf,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-c:a", "copy", "-movflags", "+faststart", out],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-1200:])


def _centre_vf(w, h):
    return (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1")


def _follow_vf(w, h, src_w, src_h, guest_cx):
    """Crop a full-height 9:16 slice centred on guest_cx (source pixels), then scale
    up to the target frame. Falls back to centre if inputs look wrong."""
    crop_w = max(1, round(src_h * w / h))          # 9:16 slice at full source height
    if crop_w >= src_w:                            # source already narrower than slice
        return _centre_vf(w, h)
    x = int(round(guest_cx - crop_w / 2))
    x = max(0, min(x, src_w - crop_w))             # clamp inside the frame
    return f"crop={crop_w}:{src_h}:{x}:0,scale={w}:{h},setsar=1"


def reframe(src: str, out: str, frame=(W, H), guest=None, use_face=True):
    """Reframe 16:9 -> 9:16. Returns the framing flag: "ok" or "review"."""
    w, h = frame

    if not use_face:
        _run_ff(_centre_vf(w, h), src, out)
        return "ok"

    try:
        from . import face                          # imported as a package
    except ImportError:
        import face                                 # run as a script

    # If we can't analyse faces, centre-crop. Flag review only when we have reason to
    # believe it's an interview (diarization found a guest) and so can't verify framing.
    if not face.detector_available():
        _run_ff(_centre_vf(w, h), src, out)
        return "review" if guest is not None else "ok"

    try:
        info = face.analyze(src)
    except Exception as e:                          # detection failed at runtime
        print(f"      ~ face-follow unavailable ({str(e)[:80]}); centre-crop")
        _run_ff(_centre_vf(w, h), src, out)
        return "review" if guest is not None else "ok"

    mode, cx = info["mode"], info["guest_cx"]
    if mode == "ambiguous" or cx is None:
        # Two comparable faces — don't guess. Centre-crop and let a producer check.
        _run_ff(_centre_vf(w, h), src, out)
        return "review"

    # single or follow: re-centre the crop on the guest.
    vf = _follow_vf(w, h, info["width"], info["height"], cx)
    _run_ff(vf, src, out)
    return "ok"


def main():
    ap = argparse.ArgumentParser(description="Stage 4: reframe 16:9 -> 9:16 (hero-focus)")
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--no-face", action="store_true", help="skip face-follow, centre-crop only")
    a = ap.parse_args()
    flag = reframe(a.src, a.out, use_face=not a.no_face)
    print(f"reframed -> {a.out}  (framing: {flag})")


if __name__ == "__main__":
    main()
