"""
KH Clipper — Stage 4: reframe (16:9 -> 9:16) with hero-focus speaker-follow.

The Shorts strategy is solo guest, host off-screen. For a centred solo guest a clean
centre-crop is plenty. For a two-person interview a centre-crop can split the frame
between host and guest, which kills the intimacy. So:

  - We track each face as a persistent subject through the clip (src/face.py,
    MediaPipe BlazeFace) — not "whichever face is biggest this frame", which in a
    two-person shot ping-pongs between host and guest and lands the crop in the gap.
  - One subject  -> re-centre the 9:16 crop on the guest (handles off-centre solos too).
  - Two-plus subjects -> follow the one who is SPEAKING (the face whose mouth moves in
    time with the audio = the guest), and lock the crop to them so the host stays out.
  - We can't tell who's speaking (both talk equally, or no lip/audio signal on
    same-size faces), OR face detection is unavailable on a diarized interview ->
    keep the centre-crop and flag the clip `framing: review` so a producer checks it.
    We never ship a bad crop silently, and never pick a subject at random.

`reframe()` keeps its original signature (src, out) plus optional `guest`/`mode`, and
returns a framing flag string: "ok" or "review".

`mode` is the Shorts Engine's `reframe` request field (kh-studio sends "speaker"):
  - "speaker" (default) -> follow the active speaker (face-follow + a smoothed pan).
  - "center"/"centre"/"static"/"none"/"off" -> plain centre-crop, no face work.

    python -m src.reframe input.mp4 output_vertical.mp4 [--no-face]
"""
from __future__ import annotations
import argparse
import json
import subprocess

W, H = 1080, 1920

# `reframe` request values that mean "don't follow anyone, just centre-crop".
_CENTRE_MODES = {"center", "centre", "static", "none", "off"}

# Manual side anchors as a fraction of source width, for the two directable modes.
# A KH interview is a standard side-by-side two-shot, so the left person sits around
# 30% of the width and the right person around 70%. The producer picks the side when
# auto speaker-tracking got it wrong; `offset` then fine-tunes it.
_SIDE_ANCHOR = {"left": 0.30, "right": 0.70}

# How far a nudge can bias the crop, as a fraction of source width (both directions).
_MAX_OFFSET = 0.35


def _probe_dims(src):
    """(width, height) of the source video via ffprobe, or (0, 0) if it can't be read
    (ffprobe missing, non-video input, etc.). Never raises — a failed probe just makes
    the caller fall back to the centre/auto crop path."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", src],
            capture_output=True, text=True)
        w, h = r.stdout.strip().split("x")[:2]
        return int(w), int(h)
    except (ValueError, IndexError, OSError):
        return 0, 0


def _run_fit(w, h, src, out):
    """Fit-both: show the WHOLE 16:9 frame inside the 9:16 canvas so both people stay in
    shot. Scale the full frame to the target width and centre it vertically; fill the band
    above and below with a blurred, zoomed copy of the same frame (the standard 'fit' look,
    no dead bars). Crops no one out, so it's the last-resort framing that always yields a
    usable clip when a tight crop would drop the host or guest."""
    fc = (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},boxblur=luma_radius=24:luma_power=1[bg];"
        f"[0:v]scale={w}:-2[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
    )
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-filter_complex", fc,
         "-map", "[v]", "-map", "0:a?",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-c:a", "copy", "-movflags", "+faststart", out],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-1200:])


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


def _write_faceband(out, info):
    """KH-MGX-001 1.3 — sidecar the guest's face band next to the reframed clip so
    caption.py can keep captions/the hook banner clear of their face. We only crop
    HORIZONTALLY here (crop=...:src_h:x:0, full source height kept), so the band's
    normalised y — computed against the SOURCE frame in face.py — survives the
    crop+scale to the output frame unchanged. Best-effort; never raises."""
    band = (info or {}).get("face_band")
    if not band:
        return
    try:
        with open(out + ".faceband.json", "w") as f:
            json.dump(band, f)
    except OSError:
        pass


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


def reframe(src: str, out: str, frame=(W, H), guest=None, mode="speaker", use_face=None,
            offset=0.0):
    """Reframe 16:9 -> 9:16. Returns the framing flag: "ok" or "review".

    `mode` is the studio `reframe` request:
      - "speaker" (default) -> follow the active speaker (face-follow + smoothed pan).
      - "center"/"centre"/… -> plain centre-crop, no face work.
      - "left"/"right"      -> a directable crop anchored on the left/right person (a
                               standard side-by-side two-shot). No face detection: this
                               is the producer's override for when auto got it wrong, so
                               it does exactly what it's told.
      - "fit"               -> show the whole frame so both people stay in shot.
    `offset` nudges the crop left (negative) or right (positive) as a fraction of source
    width, applied to speaker/center/left/right (ignored by "fit"). `use_face` overrides
    the speaker/center decision when set explicitly (CLI)."""
    w, h = frame
    m = str(mode or "speaker").strip().lower()
    off = max(-_MAX_OFFSET, min(_MAX_OFFSET, float(offset or 0.0)))

    # Fit-both: whole frame inside 9:16, both people shown. No cropping, no face work.
    if m == "fit":
        _run_fit(w, h, src, out)
        return "ok"

    # Manual side / nudge: a deterministic anchored crop the PRODUCER directs. It does
    # not use face detection on purpose — this is the override for when auto got it
    # wrong, so it must do exactly what it's told. `off` biases the anchor left/right.
    if m in _SIDE_ANCHOR or (off and m in _CENTRE_MODES):
        sw, sh = _probe_dims(src)
        if sw and sh:
            anchor = _SIDE_ANCHOR.get(m, 0.5) + off
            _run_ff(_follow_vf(w, h, sw, sh, anchor * sw), src, out)
            return "ok"
        # probe failed -> fall through to the centre/auto paths below.

    if use_face is None:
        use_face = _wants_face(m)

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

    # A producer nudge on the auto crop: bias the anchor and hold it static (a moving
    # pan fighting a manual offset would just look like drift). _follow_vf clamps x.
    if off:
        _run_ff(_follow_vf(w, h, info["width"], info["height"], cx + off * info["width"]), src, out)
        _write_faceband(out, info)
        return "ok"

    # single or follow: pan with the speaker over time, falling back to a static crop
    # centred on the guest if we can't build a pan. A malformed pan expression must
    # never cost us the clip, so retry the static crop if ffmpeg rejects the pan.
    static_vf = _follow_vf(w, h, info["width"], info["height"], cx)
    pan_vf = _track_vf(w, h, info["width"], info["height"], info.get("track"))
    _write_faceband(out, info)
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
