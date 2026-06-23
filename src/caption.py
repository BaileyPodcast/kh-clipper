"""
KH Clipper — Stage 5: caption + finish (the last mile).

Takes a reframed 9:16 clip plus the word-level transcript for that clip and
produces the FINISHED, branded, ready-to-post videos:
  - Olive Pill karaoke captions (active word turns gold)
  - gentle rotating CTAs (subscribe / full episode / linked video)
  - KH logo top-right

All burned in ONE ffmpeg pass per export. Two exports per clip:
  <name>_shorts.mp4     (CTA arrows point at YouTube's native buttons)
  <name>_universal.mp4  (branded text CTAs, no arrows — Reels/TikTok)

Captions, CTAs, logo and colours all come from src/brand.py — the single source
of truth. Reads word timings from the transcript (the data contract).

    python -m src.caption <clip.mp4> <transcript.json> --start 850.8 --end 859.4
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
from src import brand, cta

CAP = brand.CAPTION


def _t(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _c(ass_colour: str) -> str:           # &HAABBGGRR -> &HBBGGRR& for inline \c
    return "&H" + ass_colour[4:] + "&"


def _alpha(opacity: float) -> str:
    return f"{int(round((1 - opacity) * 255)):02X}"


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


def caption_events(words):
    """Olive Pill karaoke: line in cream, the currently-spoken word in gold."""
    base, active = _c(CAP["base_colour"]), _c(CAP["active_colour"])
    n = CAP["max_words_per_line"]
    lines = [words[i:i + n] for i in range(0, len(words), n)]
    events = []
    for line in lines:
        for i, w in enumerate(line):
            t0 = w["start"]
            t1 = line[i + 1]["start"] if i + 1 < len(line) else w["end"]
            if t1 <= t0:
                t1 = t0 + 0.2
            parts = [f"{{\\c{active if j == i else base}}}{ww['text']}"
                     for j, ww in enumerate(line)]
            events.append(f"Dialogue: 1,{_t(t0)},{_t(t1)},KHCap,,0,0,,{' '.join(parts)}")
    return events


def banner_events(banner, duration):
    """The on-screen hook banner: the curiosity-gap headline shown big in the upper
    third for the opening seconds (the scroll-stopper), then faded out so it doesn't
    fight the captions. One ASS event."""
    if not banner:
        return []
    t0 = 0.2
    t1 = min(5.0, max(2.5, duration * 0.55))      # hold through the hook window
    fade = "\\fad(350,350)"
    text = banner.strip().rstrip(".")             # punchy, no trailing full stop
    return [f"Dialogue: 0,{_t(t0)},{_t(t1)},Banner,,0,0,,{{{fade}}}{text}"]


def build_ass(words, duration, variant, path, frame=(1080, 1920), banner=None):
    W, H = frame
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
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV
Style: KHCap,{CAP['font']},{CAP['font_size']},{cream},{olive},{cap_box},0,3,{CAP['outline_px']},0,2,60,60,{CAP['margin_v_px']}
Style: Banner,{head_font},80,{olive_text},{gold_pill},{olive},0,3,10,0,8,90,90,360
Style: CTApill,{brand.CTA['font']},{brand.CTA['font_size']},{brand.CTA['text_colour']},{olive},{cta_box},0,3,16,0,5,40,40,0
Style: CTAarrow,{brand.CTA['font']},{brand.CTA['font_size']},{gold},{olive},&H00000000,0,1,0,0,5,0,0,0

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, Effect, Text
"""
    # When the banner owns the opening, drop the early subscribe nudge (declutter).
    events = (banner_events(banner, duration)
              + caption_events(words)
              + cta.build_cta_events(duration, variant, frame, suppress_soft=bool(banner)))
    with open(path, "w") as f:
        f.write(header + "\n".join(events) + "\n")
    return path


def _duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "json", path],
                       capture_output=True, text=True)
    return float(json.loads(r.stdout)["format"]["duration"])


def finish(clip_in, words, out_base, frame=(1080, 1920), here=None,
           variants=("shorts", "universal"), banner=None):
    """Burn captions + hook banner + CTA + logo. Writes <out_base>_<variant>.mp4 per variant."""
    here = here or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fonts_dir = os.path.join(here, "assets", "fonts")
    logo = os.path.join(here, brand.LOGO["file_on_video"])
    dur = _duration(clip_in)
    W = frame[0]
    logo_w = int(W * brand.LOGO["width_pct"])
    margin = int(W * brand.LOGO["margin_pct"])
    opa = brand.LOGO["opacity"]
    outs = []
    for variant in variants:
        ass = build_ass(words, dur, variant, f"{out_base}.{variant}.ass", frame, banner=banner)
        ass_f = ass.replace("\\", "/").replace(":", "\\:")
        fonts_f = fonts_dir.replace("\\", "/").replace(":", "\\:")
        out = f"{out_base}_{variant}.mp4"
        filt = (f"[0:v]subtitles='{ass_f}':fontsdir='{fonts_f}'[v];"
                f"[1:v]scale={logo_w}:-1,format=rgba,colorchannelmixer=aa={opa}[lg];"
                f"[v][lg]overlay=W-w-{margin}:{margin}:format=auto[out]")
        cmd = ["ffmpeg", "-y", "-i", clip_in]
        if os.path.exists(logo):
            cmd += ["-i", logo, "-filter_complex", filt, "-map", "[out]"]
        else:                                  # logo missing: captions+CTA only
            cmd += ["-vf", f"subtitles='{ass_f}':fontsdir='{fonts_f}'", "-map", "0:v"]
        cmd += ["-map", "0:a?", "-c:a", "aac", "-pix_fmt", "yuv420p", out]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-1400:])
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
