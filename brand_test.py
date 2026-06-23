#!/usr/bin/env python3
"""
KH Clipper — Brand smoke test.

Burns the Olive Pill captions + KH logo onto a short clip so you can eyeball the
full brand stack on real footage BEFORE the detect/cut stages exist.

Usage:
  python brand_test.py                      # makes a 10s synthetic test clip
  python brand_test.py path/to/clip.mp4     # use your own clip
  python brand_test.py clip.mp4 words.json  # use your own words (transcript shape)

words.json shape (same data contract as the transcriber):
  [{"text":"You","start":0.0,"end":0.4}, {"text":"are","start":0.4,"end":0.7}, ...]

Output: output/brand_test_branded.mp4  (+ a still frame to check)
This is a THROWAWAY proof. The real Stage 5 caption.py will reuse build_ass().
"""
import json
import os
import subprocess
import sys
from src import brand

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")
os.makedirs(OUT, exist_ok=True)


def _ass_time(t: float) -> str:
    h = int(t // 3600); m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _alpha_hex(opacity: float) -> str:
    # ASS alpha: 00 opaque .. FF transparent
    return f"{int(round((1 - opacity) * 255)):02X}"


def build_ass(words, path):
    """Word-level karaoke: whole line in an olive pill, active word turns gold."""
    c = brand.CAPTION
    W, H = c["frame"]
    base = c["base_colour"]
    active = c["active_colour"]
    box_alpha = _alpha_hex(c["box_opacity"])
    back = "&H" + box_alpha + c["box_colour"][2:]   # olive pill w/ opacity
    n = c["max_words_per_line"]
    lines = [words[i:i + n] for i in range(0, len(words), n)]

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV
Style: KH,{c['font']},{c['font_size']},{base},{c['outline_colour']},{back},0,3,{c['outline_px']},0,2,80,80,{c['margin_v_px']}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, Effect, Text
"""
    events = []
    for line in lines:
        for i, w in enumerate(line):
            start = w["start"]
            end = line[i + 1]["start"] if i + 1 < len(line) else w["end"]
            parts = []
            for j, ww in enumerate(line):
                col = active if j == i else base
                parts.append(f"{{\\c{col}}}{ww['text']}")
            text = " ".join(parts)
            events.append(
                f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},KH,,0,0,,{text}"
            )
    with open(path, "w") as f:
        f.write(header + "\n".join(events) + "\n")
    return path


def make_synthetic_clip(path, seconds=10):
    W, H = brand.CAPTION["frame"]
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=0x6B6F63:s={W}x{H}:d={seconds}",
        "-f", "lavfi", "-i", "sine=frequency=220:duration=" + str(seconds),
        "-shortest", "-pix_fmt", "yuv420p", path,
    ], check=True, capture_output=True)
    return path


DEMO_WORDS = [
    {"text": "You", "start": 0.4, "end": 1.1},
    {"text": "are", "start": 1.1, "end": 1.7},
    {"text": "not", "start": 1.7, "end": 2.4},
    {"text": "broken", "start": 2.4, "end": 3.4},
    {"text": "you", "start": 3.8, "end": 4.4},
    {"text": "are", "start": 4.4, "end": 5.0},
    {"text": "still", "start": 5.0, "end": 5.7},
    {"text": "here", "start": 5.7, "end": 6.8},
]


def main():
    clip = sys.argv[1] if len(sys.argv) > 1 else None
    if clip and len(sys.argv) > 2:
        words = json.load(open(sys.argv[2]))
    else:
        words = DEMO_WORDS
    if not clip:
        clip = os.path.join(OUT, "brand_test_source.mp4")
        print("No clip given — making a 10s synthetic test clip...")
        make_synthetic_clip(clip)

    ass = build_ass(words, os.path.join(OUT, "brand_test.ass"))
    fonts_dir = os.path.join(HERE, "assets", "fonts")
    logo = os.path.join(HERE, brand.LOGO["file_on_video"])
    out = os.path.join(OUT, "brand_test_branded.mp4")

    W = brand.CAPTION["frame"][0]
    logo_w = int(W * brand.LOGO["width_pct"])
    margin = int(W * brand.LOGO["margin_pct"])
    opa = brand.LOGO["opacity"]

    # escape the ass path for the subtitles filter
    ass_f = ass.replace("\\", "/").replace(":", "\\:")
    fonts_f = fonts_dir.replace("\\", "/").replace(":", "\\:")

    filt = (
        f"[0:v]subtitles='{ass_f}':fontsdir='{fonts_f}'[v];"
        f"[1:v]scale={logo_w}:-1,format=rgba,colorchannelmixer=aa={opa}[lg];"
        f"[v][lg]overlay=W-w-{margin}:{margin}:format=auto[out]"
    )
    cmd = [
        "ffmpeg", "-y", "-i", clip, "-i", logo,
        "-filter_complex", filt, "-map", "[out]", "-map", "0:a?",
        "-c:a", "copy", "-pix_fmt", "yuv420p", out,
    ]
    print("Burning captions + logo...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("FFMPEG ERROR:\n", r.stderr[-1500:])
        sys.exit(1)

    frame = os.path.join(OUT, "brand_test_frame.png")
    subprocess.run([
        "ffmpeg", "-y", "-ss", "2.8", "-i", out, "-frames:v", "1", frame,
    ], check=True, capture_output=True)
    print("DONE")
    print("  clip :", out)
    print("  frame:", frame)


if __name__ == "__main__":
    main()
