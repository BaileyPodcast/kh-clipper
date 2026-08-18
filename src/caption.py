"""
KH Clipper — Stage 5: caption + finish (the last mile).

Takes a reframed 9:16 clip plus the word-level transcript for that clip and
produces the FINISHED, branded, ready-to-post videos:
  - Olive Pill KINETIC captions (word pop-in, active word turns gold + scales
    up, the clip's highlight_word stays gold/oversized, new lines fade in)
  - face-aware caption + hook-banner placement (never overlaps the guest's face)
  - a gentle, alternating-direction punch-in (Ken Burns zoom), optional
  - gentle rotating CTAs (subscribe / full episode / linked video)
  - KH logo top-right

Trauma-informed (KH-TIC-001, KH-MGX-001 locked decision #3): a clip whose
safety rating isn't "ok" renders with the CALM preset — fades only, no pop,
no scale, no zoom. Pass the clip's `safety` in; energy never overrides dignity.

All burned in ONE ffmpeg pass per export. Two exports per clip:
  <name>_shorts.mp4     (CTA arrows point at YouTube's native buttons)
  <name>_universal.mp4  (branded text CTAs, no arrows — Reels/TikTok)

Captions, CTAs, logo, colours and every animation timing/scale all come from
src/brand.py (brand.ANIMATION) — the single source of truth. No magic numbers
here. Reads word timings from the transcript (the data contract).

    python -m src.caption <clip.mp4> <transcript.json> --start 850.8 --end 859.4
"""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
from src import brand, cta, loudness

CAP = brand.CAPTION
ANIM = brand.ANIMATION


def _t(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _c(ass_colour: str) -> str:           # &HAABBGGRR -> &HBBGGRR& for inline \c
    return "&H" + ass_colour[4:] + "&"


def _alpha(opacity: float) -> str:
    return f"{int(round((1 - opacity) * 255)):02X}"


def _fscale(pct) -> str:
    """ASS scale override for both axes, e.g. 108 -> '\\fscx108\\fscy108'."""
    return f"\\fscx{pct}\\fscy{pct}"


def _clean_word(text: str) -> str:
    """Lowercase, punctuation-stripped word for matching against detect.py's
    highlight_word (itself produced by the same [a-z']+ tokenisation)."""
    return re.sub(r"[^a-z']", "", (text or "").lower())


def _preset_for(safety: str | None):
    """CALM for anything the trauma-informed gate hasn't cleared as 'ok'
    (KH-TIC-001 / KH-MGX-001 locked decision #3). Returns (name, config)."""
    name = "calm" if (safety or "ok") != "ok" else "standard"
    return name, ANIM["presets"][name]


def _band_for_captions(faceband):
    """Pick the caption MarginV for the WHOLE clip from the guest's face band
    (1.3) — one decision per clip, never per frame. Falls back to the default
    when there's no sidecar (no face data, or a mode that doesn't produce one)."""
    b = ANIM["caption_bands"]
    if not faceband or faceband.get("bottom") is None:
        return b["default_margin_v_px"]
    if faceband["bottom"] >= b["low_face_threshold"]:
        return b["raised_margin_v_px"]
    return b["default_margin_v_px"]


def _band_for_banner(faceband):
    """Same idea for the hook banner (upper third), checked against the face TOP."""
    b = ANIM["banner_bands"]
    if not faceband or faceband.get("top") is None:
        return b["default_margin_v_px"]
    if faceband["top"] <= b["high_face_threshold"]:
        return b["mid_margin_v_px"]
    return b["default_margin_v_px"]


def _load_faceband(clip_in: str):
    """Load the per-clip guest face band written by reframe.py (1.3), or None
    when unavailable — we fall back to the default band, never guess a jittery
    per-frame position."""
    path = clip_in + ".faceband.json"
    if not os.path.exists(path):
        return None
    try:
        data = json.load(open(path))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and data.get("top") is not None and data.get("bottom") is not None:
        return data
    return None


def clip_words(words, start, end):
    """Filter transcript words to [start,end] and rebase to clip-relative time."""
    out = []
    for w in words:
        ws, we = float(w["start"]), float(w["end"])
        if we <= start or ws >= end:
            continue
        out.append({"text": w["text"],
                    "start": max(0.0, ws - start),
                    "end": max(0.0, min(we, end) - start)})
    return out


def caption_events(words, highlight_word=None, preset=None):
    """Olive Pill KINETIC captions: line in cream, the currently-spoken word in
    gold with a pop-in entrance (1.1); the clip's highlight_word (detect.py)
    stays gold and oversized whenever it's on screen, active or not (1.2). New
    LINES fade in instead of hard-cutting (1.1). Every timing/scale comes from
    brand.ANIMATION; `preset` (brand.ANIMATION["presets"][...]) gates the pop
    and highlight effects — the CALM preset renders colour changes only."""
    preset = preset or ANIM["presets"]["standard"]
    base, active = _c(CAP["base_colour"]), _c(CAP["active_colour"])
    n = CAP["max_words_per_line"]
    lines = [words[i:i + n] for i in range(0, len(words), n)]
    hl = _clean_word(highlight_word) if (preset["highlight"] and highlight_word) else None
    pop_ms = ANIM["pop_ms"]
    fade_ms = preset["fade_ms"]

    events = []
    for line in lines:
        for i, w in enumerate(line):
            t0 = w["start"]
            t1 = line[i + 1]["start"] if i + 1 < len(line) else w["end"]
            if t1 <= t0:
                t1 = t0 + 0.2
            runs = []
            for j, ww in enumerate(line):
                is_active = (j == i)
                is_hl = bool(hl) and _clean_word(ww["text"]) == hl
                if is_active:
                    target = ANIM["highlight_scale"] if is_hl else ANIM["active_scale"]
                    if preset["pop"]:
                        # pop in from pop_from_scale -> target over pop_ms, ms relative
                        # to THIS event's own start (the word's spoken onset); returns
                        # to rest scale automatically when the next word's event begins.
                        tags = (f"\\c{active}{_fscale(ANIM['pop_from_scale'])}"
                                f"\\t(0,{pop_ms},{_fscale(target)})")
                    else:                                   # CALM: colour only, no scale
                        tags = f"\\c{active}"
                    runs.append(f"{{{tags}}}{ww['text']}")
                elif is_hl:                                 # emphasis word, not active
                    runs.append(f"{{\\c{active}{_fscale(ANIM['highlight_scale'])}}}{ww['text']}")
                else:
                    runs.append(f"{{\\c{base}}}{ww['text']}")
            text = " ".join(runs)
            if i == 0:                                      # new line -> fade in
                text = f"{{\\fad({fade_ms},0)}}" + text
            events.append(f"Dialogue: 1,{_t(t0)},{_t(t1)},KHCap,,0,0,,{text}")
    return events


def banner_events(banner, duration):
    """The on-screen hook banner: the curiosity-gap headline shown big for the
    opening seconds (the scroll-stopper), then faded out so it doesn't fight the
    captions. One ASS event. Starts on FRAME 1 (t=0.0, the hook must exist the
    instant the feed shows the clip) and holds at least 3 seconds."""
    if not banner:
        return []
    t0 = 0.0
    t1 = min(5.0, max(3.0, duration * 0.55))      # hold through the hook window
    fade = "\\fad(350,350)"
    text = banner.strip().rstrip(".")             # punchy, no trailing full stop
    return [f"Dialogue: 0,{_t(t0)},{_t(t1)},Banner,,0,0,,{{{fade}}}{text}"]


def build_ass(words, duration, variant, path, frame=(1080, 1920), banner=None,
              highlight_word=None, preset=None, faceband=None, loopable=False):
    W, H = frame
    preset = preset or ANIM["presets"]["standard"]
    cream, olive = CAP["base_colour"], CAP["outline_colour"]
    cap_box = "&H" + _alpha(CAP["box_opacity"]) + CAP["box_colour"][2:]
    cta_box = "&H" + _alpha(brand.CTA["pill_opacity"]) + brand.CTA["pill_colour"][2:]
    gold = brand.CTA["accent_colour"]
    # Hook banner: dark-olive text on a gold pill — high contrast, distinct from the
    # cream-on-olive captions. Archivo heading, top-centre, below the logo.
    # With BorderStyle=3 the PILL colour is the OutlineColour, so gold goes there.
    olive_text = brand.COLOURS["dark_olive"]["ass"]
    gold_pill = brand.COLOURS["gold"]["ass"]      # opaque brand gold (the box)
    head_font = brand.FONTS["heading"]["family"]
    # 1.3 — face-aware placement: ONE band choice for the whole clip (never per
    # frame), from the guest's face band sidecar reframe.py wrote (or the default
    # when there's no sidecar).
    cap_margin_v = _band_for_captions(faceband)
    banner_margin_v = _band_for_banner(faceband)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV
Style: KHCap,{CAP['font']},{CAP['font_size']},{cream},{olive},{cap_box},0,3,{CAP['outline_px']},0,2,60,60,{cap_margin_v}
Style: Banner,{head_font},80,{olive_text},{gold_pill},{olive},0,3,10,0,8,90,90,{banner_margin_v}
Style: CTApill,{brand.CTA['font']},{brand.CTA['font_size']},{brand.CTA['text_colour']},{olive},{cta_box},0,3,16,0,5,40,40,0
Style: CTAarrow,{brand.CTA['font']},{brand.CTA['font_size']},{gold},{olive},&H00000000,0,1,0,0,5,0,0,0

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, Effect, Text
"""
    # When the banner owns the opening, drop the early subscribe nudge (declutter).
    # `loopable` lets cta.py skip the end cards on a short seamless-loop clip so
    # the loop seam stays a clean hard cut.
    events = (banner_events(banner, duration)
              + caption_events(words, highlight_word=highlight_word, preset=preset)
              + cta.build_cta_events(duration, variant, frame, suppress_soft=bool(banner),
                                     loopable=loopable))
    with open(path, "w") as f:
        f.write(header + "\n".join(events) + "\n")
    return path


def _duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "json", path],
                       capture_output=True, text=True)
    return float(json.loads(r.stdout)["format"]["duration"])


def _fps(path):
    """Source frame rate, for the punch-in zoompan filter (matching it keeps video
    timing exact). Defaults to 30 if ffprobe can't read it — never fatal."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0",
                            path], capture_output=True, text=True)
        num, _, den = r.stdout.strip().partition("/")
        fps = float(num) / float(den) if den else float(num)
        return fps if fps > 0 else 30.0
    except (ValueError, OSError):
        return 30.0


def _punch_in_filter(duration, frame, fps, direction):
    """1.4 — gentle, alternating-direction Ken Burns punch-in (scale+crop, driven
    by zoompan with d=1 so it re-samples the WHOLE clip rather than a still).
    Returns an ffmpeg filter fragment (no leading stream label) or None when
    there's nothing sensible to build (caller then skips the effect entirely
    rather than risk a bad render)."""
    p = ANIM["punch_in"]
    z0, z1 = p["start_scale"], p["end_scale"]
    if duration <= 0 or z1 <= z0 or fps <= 0:
        return None
    frames = max(2, round(duration * fps))
    step = (z1 - z0) / frames
    bias = 0.44 if direction == "left" else 0.56       # alternates per clip
    w, h = frame
    zoom_expr = f"min(zoom+{step:.6f}\\,{z1})"
    x_expr = f"iw*{bias}-(iw/zoom/2)"
    y_expr = "ih/2-(ih/zoom/2)"
    return f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d=1:s={w}x{h}:fps={fps:.3f}"


# The SHIPPING encode: pinned so the final export never falls to ffmpeg's
# default CRF 23 while every earlier stage encodes CRF 18. One list, used by
# both the normal and the punch-in-retry command.
FINAL_ENCODE = ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart"]


def finish(clip_in, words, out_base, frame=(1080, 1920), here=None,
           variants=("shorts", "universal"), banner=None,
           highlight_word=None, safety="ok", clip_index=0, loopable=False):
    """Burn kinetic captions + hook banner + CTA + logo (+ punch-in). Writes
    <out_base>_<variant>.mp4 per variant.

    `safety` selects the animation preset (CALM for anything but "ok" —
    KH-TIC-001). `highlight_word` is detect.py's emphasis token. `clip_index`
    alternates the punch-in direction across a run's clips. `loopable` (from
    the rerank result) lets cta.py drop the end cards on a short seamless-loop
    clip so the loop seam stays a clean hard cut."""
    here = here or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fonts_dir = os.path.join(here, "assets", "fonts")
    logo = os.path.join(here, brand.LOGO["file_on_video"])
    dur = _duration(clip_in)
    W = frame[0]
    logo_w = int(W * brand.LOGO["width_pct"])
    margin = int(W * brand.LOGO["margin_pct"])
    opa = brand.LOGO["opacity"]

    _, preset = _preset_for(safety)
    faceband = _load_faceband(clip_in)

    punch = None
    if preset["punch_in"] and ANIM["punch_in"]["enabled"]:
        direction = "left" if (clip_index % 2 == 0) else "right"
        try:
            punch = _punch_in_filter(dur, frame, _fps(clip_in), direction)
        except Exception as e:                          # never cost the whole clip
            print(f"      ~ punch-in skipped ({str(e)[:80]})")
            punch = None

    outs = []
    for variant in variants:
        ass = build_ass(words, dur, variant, f"{out_base}.{variant}.ass", frame,
                         banner=banner, highlight_word=highlight_word, preset=preset,
                         faceband=faceband, loopable=loopable)
        ass_f = ass.replace("\\", "/").replace(":", "\\:")
        fonts_f = fonts_dir.replace("\\", "/").replace(":", "\\:")
        out = f"{out_base}_{variant}.mp4"
        video_src = f"[0:v]{punch}[zoomed];[zoomed]" if punch else "[0:v]"
        video_vf = f"{punch},subtitles='{ass_f}':fontsdir='{fonts_f}'" if punch \
            else f"subtitles='{ass_f}':fontsdir='{fonts_f}'"
        filt = (f"{video_src}subtitles='{ass_f}':fontsdir='{fonts_f}'[v];"
                f"[1:v]scale={logo_w}:-1,format=rgba,colorchannelmixer=aa={opa}[lg];"
                f"[v][lg]overlay=W-w-{margin}:{margin}:format=auto[out]")
        cmd = ["ffmpeg", "-y", "-i", clip_in]
        if os.path.exists(logo):
            cmd += ["-i", logo, "-filter_complex", filt, "-map", "[out]"]
        else:                                  # logo missing: captions+CTA (+ punch-in) only
            cmd += ["-vf", video_vf, "-map", "0:v"]
        cmd += ["-map", "0:a?", "-c:a", "aac"] + FINAL_ENCODE + [out]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 and punch:
            # A bad punch-in expression must never cost us the clip — retry flat.
            print(f"      ~ punch-in render failed; retrying without it")
            cmd = ["ffmpeg", "-y", "-i", clip_in]
            flat_filt = (f"[0:v]subtitles='{ass_f}':fontsdir='{fonts_f}'[v];"
                         f"[1:v]scale={logo_w}:-1,format=rgba,colorchannelmixer=aa={opa}[lg];"
                         f"[v][lg]overlay=W-w-{margin}:{margin}:format=auto[out]")
            if os.path.exists(logo):
                cmd += ["-i", logo, "-filter_complex", flat_filt, "-map", "[out]"]
            else:
                cmd += ["-vf", f"subtitles='{ass_f}':fontsdir='{fonts_f}'", "-map", "0:v"]
            cmd += ["-map", "0:a?", "-c:a", "aac"] + FINAL_ENCODE + [out]
            r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-1400:])
        loudness.normalize(out)         # -14 LUFS for YouTube; non-fatal on failure
        outs.append(out)
        print(f"  finished {os.path.basename(out)}")
    return outs


def main():
    ap = argparse.ArgumentParser(description="Stage 5: caption + CTA + logo")
    ap.add_argument("clip", help="reframed 9:16 clip")
    ap.add_argument("transcript", help="transcript.json with word timings")
    ap.add_argument("--start", type=float, required=True, help="clip start in source secs")
    ap.add_argument("--end", type=float, required=True, help="clip end in source secs")
    ap.add_argument("--out", default=None, help="output base path (no extension)")
    args = ap.parse_args()
    words_all = json.load(open(args.transcript)).get("words", [])
    words = clip_words(words_all, args.start, args.end)
    out_base = args.out or os.path.splitext(args.clip)[0]
    finish(args.clip, words, out_base)
    print("done")


if __name__ == "__main__":
    main()
