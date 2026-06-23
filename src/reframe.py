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

`reframe()` keeps its original signature (src, out) plus optional `guest`/`mode`, and
returns a framing flag string: "ok" or "review".

`mode` is the Shorts Engine's `reframe` request field (kh-studio sends "speaker"):
  - "speaker" (default) -> follow the active speaker (face-follow + a smoothed pan).
  - "center"/"centre"/"static"/"none"/"off" -> plain centre-crop, no face work.

    python -m src.reframe input.mp4 output_vertical.mp4 [--no-face]
"""
from __future__ import annotations
import argparse
import subprocess

W, H = 1080, 1920

# `reframe` request values that mean "don't follow anyone, just centre-crop".
_CENTRE_MODES = {"center", "centre", "static", "none", "off"}


def _wants_face(mode):
    """Map the studio `reframe` field to whether we run speaker-follow. Unknown/empty
    values default to following (the product default is speaker-tracked Shorts)."""
    if mode is None:
        return True
    return str(mode).strip().lower() not in _CENTRE_MODES


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


def _smooth(xs, window=3):
    """Moving-average smooth so the pan glides instead of jittering frame-to-frame."""
    if window <= 1 or len(xs) < 2:
        return list(xs)
    half = window // 2
    out = []
    for i in range(len(xs)):
        lo, hi = max(0, i - half), min(len(xs), i + half + 1)
        out.append(sum(xs[lo:hi]) / (hi - lo))
    return out


def _pan_keyframes(track, crop_w, src_w, max_kf=24):
    """Turn a per-frame face track into a small set of clamped (t, x) keyframes for a
    smoothed pan. x is the crop's left edge. Returns None when there isn't enough signal
    to pan (caller falls back to the static follow/centre crop).

    Keyframe x values are pre-clamped to [0, src_w-crop_w]; because linear interpolation
    between two in-range values stays in range, the runtime expression can never crop
    outside the frame — no min/max guard needed in the ffmpeg expr."""
    pts = sorted((t, cx) for (t, cx) in (track or []) if cx is not None)
    lo, hi = 0, src_w - crop_w
    if len(pts) < 4 or hi <= lo:
        return None
    xs = [int(round(min(hi, max(lo, x - crop_w / 2.0)))) for x in _smooth([cx for _, cx in pts])]
    ts = [t for t, _ in pts]
    n = len(xs)
    # Downsample evenly to at most max_kf keyframes (always keep first + last).
    idx = sorted(set(round(i * (n - 1) / (max_kf - 1)) for i in range(max_kf))) if n > max_kf \
        else list(range(n))
    kf = [(round(ts[i], 2), xs[i]) for i in idx]
    # Drop keyframes that barely move (keeps the expression short) but keep the last one.
    pruned = [kf[0]]
    for j, (t, x) in enumerate(kf[1:], 1):
        if abs(x - pruned[-1][1]) >= 2 or j == len(kf) - 1:
            pruned.append((t, x))
    return pruned if len(pruned) >= 2 else None


def _pan_expr(kf):
    r"""Build an ffmpeg crop-x expression: a piecewise-linear pan across the keyframes.
    Commas inside the expression are escaped (\,) so the filtergraph parser keeps them
    in the expression instead of reading them as option separators."""
    e = f"{kf[-1][1]}"                              # past the last keyframe: hold final x
    for k in range(len(kf) - 2, -1, -1):
        (t0, x0), (t1, _) = kf[k], kf[k + 1]
        slope = (kf[k + 1][1] - x0) / (t1 - t0) if t1 > t0 else 0.0
        lin = f"({x0}+({slope:.4f})*(t-{t0}))"
        e = f"if(lt(t\\,{t1})\\,{lin}\\,{e})"
    return f"if(lt(t\\,{kf[0][0]})\\,{kf[0][1]}\\,{e})"   # before first keyframe: hold x0


def _track_vf(w, h, src_w, src_h, track):
    """Time-varying 9:16 crop that pans to follow the speaker across the clip. Returns a
    -vf string, or None if there isn't enough track to pan (caller uses the static crop)."""
    crop_w = max(1, round(src_h * w / h))
    if crop_w >= src_w:
        return None
    kf = _pan_keyframes(track, crop_w, src_w)
    if not kf:
        return None
    return f"crop=w={crop_w}:h={src_h}:x={_pan_expr(kf)}:y=0,scale={w}:{h},setsar=1"


def reframe(src: str, out: str, frame=(W, H), guest=None, mode="speaker", use_face=None):
    """Reframe 16:9 -> 9:16. Returns the framing flag: "ok" or "review".

    `mode` is the studio `reframe` request ("speaker" follows the speaker, "center"
    centre-crops). `use_face` overrides that decision when set explicitly (CLI)."""
    w, h = frame
    if use_face is None:
        use_face = _wants_face(mode)

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

    face_mode, cx = info["mode"], info["guest_cx"]
    if face_mode == "ambiguous" or cx is None:
        # Two comparable faces — don't guess. Centre-crop and let a producer check.
        _run_ff(_centre_vf(w, h), src, out)
        return "review"

    # single or follow: pan with the speaker over time, falling back to a static crop
    # centred on the guest if we can't build a pan. A malformed pan expression must
    # never cost us the clip, so retry the static crop if ffmpeg rejects the pan.
    static_vf = _follow_vf(w, h, info["width"], info["height"], cx)
    pan_vf = _track_vf(w, h, info["width"], info["height"], info.get("track"))
    if pan_vf is None:
        _run_ff(static_vf, src, out)
        return "ok"
    try:
        _run_ff(pan_vf, src, out)
    except RuntimeError as e:
        print(f"      ~ speaker pan failed ({str(e)[:60]}); static guest crop")
        _run_ff(static_vf, src, out)
    return "ok"


def main():
    ap = argparse.ArgumentParser(description="Stage 4: reframe 16:9 -> 9:16 (hero-focus)")
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--no-face", action="store_true", help="skip face-follow, centre-crop only")
    a = ap.parse_args()
    flag = reframe(a.src, a.out, mode="center" if a.no_face else "speaker")
    print(f"reframed -> {a.out}  (framing: {flag})")


if __name__ == "__main__":
    main()
