"""
KH Clipper — Call-to-action overlays.

Grows the channel gently and on-brand. Generates ASS overlay events for the
"gentle rotating" pattern: a soft Subscribe nudge early, a clean middle, then
two end cards (Subscribe, then Watch the full episode + linked-video reminder)
in the last few seconds.

Two variants:
  - "shorts":    gold arrows point at YouTube's native buttons (subscribe,
                 channel profile, linked/related video). Targets live in
                 brand.CTA["shorts_targets"] — tune them once on a real device.
  - "universal": branded text only, no arrows, includes the @handle. For
                 Reels/TikTok where the native buttons sit elsewhere.

Built as ASS so it shares libass with the captions and can be merged into one
subtitle pass later (build_cta_events) or burned on its own (build_cta_ass).
"""
from __future__ import annotations
import os
from src import brand

C = brand.CTA


def _t(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _ass_to_c(ass_colour: str) -> str:
    """&HAABBGGRR  ->  &HBBGGRR&  for inline \\1c / \\3c overrides."""
    return "&H" + ass_colour[4:] + "&"


def _alpha(opacity: float) -> str:
    return f"{int(round((1 - opacity) * 255)):02X}"


def _pill(text, x, y, t0, t1, accent_word=None):
    fade = f"\\fad({C['fade_ms']},{C['fade_ms']})"
    body = text
    if accent_word and accent_word in text:
        gold = _ass_to_c(C["accent_colour"])
        cream = _ass_to_c(C["text_colour"])
        body = text.replace(accent_word, f"{{\\1c{gold}}}{accent_word}{{\\1c{cream}}}")
    return (f"Dialogue: 0,{_t(t0)},{_t(t1)},CTApill,,0,0,,"
            f"{{\\pos({x},{y})\\an5{fade}}}{body}")


def _arrow(x, y, t0, t1, size=86):
    """A gold down-arrow drawn as an ASS vector, centred on (x, y)."""
    gold = _ass_to_c(C["accent_colour"])
    H = size // 2
    shape = f"m -12 -{H} l 12 -{H} l 12 0 l 30 0 l 0 {H} l -30 0 l -12 0"
    fade = f"\\fad({C['fade_ms']},{C['fade_ms']})"
    return (f"Dialogue: 0,{_t(t0)},{_t(t1)},CTAarrow,,0,0,,"
            f"{{\\pos({x},{y})\\an5\\1c{gold}{fade}\\p1}}{shape}{{\\p0}}")


def build_cta_events(duration: float, variant: str = "shorts",
                     frame=(1080, 1920), suppress_soft: bool = False) -> list[str]:
    W, H = frame
    cx = W // 2
    cp = C["copy"]
    tgt = C["shorts_targets"]
    shorts = (variant == "shorts")
    events: list[str] = []

    # 1) Early soft nudge (text only, sits high, clear of captions). Skipped when a
    # hook banner owns the opening seconds.
    s0, s1 = C["soft_window"]
    s1 = min(s1, max(s0 + 1, duration - 1))
    if duration > 7 and not suppress_soft:
        events.append(_pill(cp["subscribe_soft"], cx, C["soft_y"], s0, s1,
                            accent_word="Subscribe"))

    # 2) End cards (last end_window_sec, split into two rotating cards)
    win = min(C["end_window_sec"], max(2.0, duration - (s1 + 0.5)))
    e0 = duration - win
    mid = e0 + win / 2
    card_y = H - 800                     # sits above the low captions; both clear the
                                         # native bottom UI (captions stack below)

    # Card A — Subscribe
    events.append(_pill(cp["subscribe"], cx, card_y, e0, mid, accent_word="subscribe"))
    if shorts:
        bx, by = tgt["subscribe_btn"]
        events.append(_arrow(bx, by - 70, e0, mid))
    else:
        events.append(_pill(cp["handle"], cx, card_y + 96, e0, mid))

    # Card B — Watch the full episode + linked-video reminder
    events.append(_pill(cp["full_episode"], cx, card_y, mid, duration,
                        accent_word="full"))
    events.append(_pill(cp["related"], cx, card_y + 96, mid, duration))
    if shorts:
        px, py = tgt["channel_profile"]
        events.append(_arrow(px, py - 70, mid, duration))
        rx, ry = tgt["related_link"]
        events.append(_arrow(rx, ry - 60, mid, duration, size=64))

    return events


def _header(frame):
    W, H = frame
    cream = C["text_colour"]
    olive = C["pill_colour"]
    box = "&H" + _alpha(C["pill_opacity"]) + C["pill_colour"][2:]
    gold = C["accent_colour"]
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV
Style: CTApill,{C['font']},{C['font_size']},{cream},{olive},{box},0,3,16,0,5,40,40,0
Style: CTAarrow,{C['font']},{C['font_size']},{gold},{olive},&H00000000,0,1,0,0,5,0,0,0

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, Effect, Text
"""


def build_cta_ass(duration: float, path: str, variant: str = "shorts",
                  frame=(1080, 1920)) -> str:
    events = build_cta_events(duration, variant=variant, frame=frame)
    with open(path, "w") as f:
        f.write(_header(frame) + "\n".join(events) + "\n")
    return path


def burn(video_in: str, video_out: str, variant: str = "shorts",
         duration: float | None = None, fonts_dir: str = "assets/fonts"):
    """Burn CTA overlays onto a video (standalone). Captions are a separate pass."""
    import subprocess, json
    if duration is None:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "json", video_in],
                           capture_output=True, text=True)
        duration = float(json.loads(r.stdout)["format"]["duration"])
    ass = build_cta_ass(duration, video_out + ".cta.ass", variant=variant)
    ass_f = ass.replace("\\", "/").replace(":", "\\:")
    fonts_f = fonts_dir.replace("\\", "/").replace(":", "\\:")
    cmd = ["ffmpeg", "-y", "-i", video_in, "-vf",
           f"subtitles='{ass_f}':fontsdir='{fonts_f}'",
           "-c:a", "copy", "-pix_fmt", "yuv420p", video_out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-1200:])
    return video_out
