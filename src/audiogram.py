"""
KH Clipper — Stage 5b: branded audiograms (opt-in).

Each KH series has its own finished 3000x3000 cover (title, its own waveform, and
the logo). The audiogram showcases that cover:

  - SQUARE (1080x1080) — the cover as-is, for feed / LinkedIn. The design is
    complete and self-contained, so no captions: keep it clean.
  - VERTICAL (1080x1920) — the cover sits on top, with the Olive Pill karaoke
    captions in a clean band below it, for Shorts / Reels (muted autoplay, where
    captions stop the scroll and add accessibility).

We don't add a second waveform: the series cover already has its own. Artwork lives
at assets/artwork/<series>.png (or .jpg); pass --series <series> on the run.

    python -m src.audiogram <clip.mp4> <transcript.json> --start S --end E --series golden-threads
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess

try:
    from . import brand, caption
except ImportError:
    import brand, caption

CAP = brand.CAPTION
COL = brand.COLOURS

SQUARE = (1080, 1080)
VERTICAL = (1080, 1920)

ARTWORK_DIR = "assets/artwork"          # assets/artwork/<series>.{png,jpg} (3000x3000)


def artwork_path(series, here):
    if not series:
        return None
    for ext in (".png", ".jpg", ".jpeg"):
        p = os.path.join(here, ARTWORK_DIR, f"{series}{ext}")
        if os.path.exists(p):
            return p
    return None


def _bg_colour(art, default):
    """Sample the cover's background colour (corners) so the caption band below it
    blends seamlessly, whatever the series' ground colour is."""
    try:
        from PIL import Image
        im = Image.open(art).convert("RGB")
        w, h = im.size
        px = im.load()
        pts = [(4, 4), (w - 5, 4), (4, h - 5), (w - 5, h - 5)]   # four corners
        r = sum(px[x, y][0] for x, y in pts) // len(pts)
        g = sum(px[x, y][1] for x, y in pts) // len(pts)
        b = sum(px[x, y][2] for x, y in pts) // len(pts)
        return f"#{r:02X}{g:02X}{b:02X}"
    except Exception:
        return default


def build_ass(words, frame, cap_marginv, path):
    """Olive Pill karaoke captions for the vertical audiogram's lower band."""
    W, H = frame
    cream = CAP["base_colour"]
    olive = CAP["outline_colour"]
    cap_box = "&H" + caption._alpha(CAP["box_opacity"]) + CAP["box_colour"][2:]
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV
Style: KHCap,{CAP['font']},{CAP['font_size']},{cream},{olive},{cap_box},0,3,{CAP['outline_px']},0,2,60,60,{cap_marginv}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, Effect, Text
"""
    with open(path, "w") as f:
        f.write(header + "\n".join(caption.caption_events(words)) + "\n")
    return path


def _render_square(clip_in, out_path, frame, here, art):
    """Square audiogram: the series cover, full frame, no captions."""
    W, H = frame
    dur = caption._duration(clip_in)
    has_art = bool(art) and os.path.exists(art)
    if has_art:
        filt = f"[1:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[out]"
    else:
        filt = f"color=c={COL['gold']['hex']}:s={W}x{H}:d={dur:.2f}[out]"
    cmd = ["ffmpeg", "-y", "-i", clip_in]
    if has_art:
        cmd += ["-loop", "1", "-i", art]
    cmd += ["-filter_complex", filt, "-map", "[out]", "-map", "0:a",
            "-t", f"{dur:.2f}", "-c:a", "aac", "-pix_fmt", "yuv420p", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-1400:])
    return out_path


def _render_vertical(clip_in, words, out_path, frame, here, art):
    """Vertical audiogram: cover (square) on top, karaoke captions in a band below."""
    W, H = frame
    dur = caption._duration(clip_in)
    has_art = bool(art) and os.path.exists(art)
    # Backing matches the cover's own ground colour so there's no seam below it.
    bg = _bg_colour(art, COL["cream_white"]["hex"]) if has_art else COL["cream_white"]["hex"]

    art_y = int(H * 0.03)                # cover sits near the top, full width (square)
    cap_marginv = int(H * 0.15)          # captions centred in the lower band
    ass = build_ass(words, frame, cap_marginv, out_path.replace(".mp4", ".ass"))
    ass_f = ass.replace("\\", "/").replace(":", "\\:")
    fonts_f = os.path.join(here, "assets", "fonts").replace("\\", "/").replace(":", "\\:")

    has_art = bool(art) and os.path.exists(art)
    filt = f"color=c={bg}:s={W}x{H}:d={dur:.2f}[bg];"
    if has_art:
        filt += f"[1:v]scale={W}:{W}:force_original_aspect_ratio=increase,crop={W}:{W},setsar=1[art];"
    else:
        filt += f"color=c={COL['gold']['hex']}:s={W}x{W}:d={dur:.2f}[art];"
    filt += (
        f"[bg][art]overlay=0:{art_y}[withart];"
        f"[withart]subtitles='{ass_f}':fontsdir='{fonts_f}'[out]"
    )
    cmd = ["ffmpeg", "-y", "-i", clip_in]
    if has_art:
        cmd += ["-loop", "1", "-i", art]
    cmd += ["-filter_complex", filt, "-map", "[out]", "-map", "0:a",
            "-t", f"{dur:.2f}", "-c:a", "aac", "-pix_fmt", "yuv420p", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-1400:])
    return out_path


def render(clip_in, words, out_base, series=None, here=None):
    """Render branded audiograms for a clip: a square cover (no captions) and a
    vertical cover + captions. Returns the list of output paths."""
    here = here or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    art = artwork_path(series, here)
    if series and not art:
        print(f"      ~ no artwork for series '{series}' "
              f"(expected {ARTWORK_DIR}/{series}.png); using fallback card")
    sq = f"{out_base}_audiogram_square.mp4"
    vt = f"{out_base}_audiogram_vertical.mp4"
    _render_square(clip_in, sq, SQUARE, here, art)
    print(f"  audiogram {os.path.basename(sq)}")
    _render_vertical(clip_in, words, vt, VERTICAL, here, art)
    print(f"  audiogram {os.path.basename(vt)}")
    return [sq, vt]


def main():
    ap = argparse.ArgumentParser(description="Stage 5b: branded audiogram (series cover)")
    ap.add_argument("clip", help="clip mp4 (audio source)")
    ap.add_argument("transcript", help="transcript.json with word timings")
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--end", type=float, required=True)
    ap.add_argument("--series", default=None, help="series name -> assets/artwork/<series>.png")
    ap.add_argument("--out", default=None, help="output base path (no extension)")
    a = ap.parse_args()
    words_all = json.load(open(a.transcript)).get("words", [])
    words = caption.clip_words(words_all, a.start, a.end)
    out_base = a.out or os.path.splitext(a.clip)[0]
    render(a.clip, words, out_base, series=a.series)
    print("done")


if __name__ == "__main__":
    main()
