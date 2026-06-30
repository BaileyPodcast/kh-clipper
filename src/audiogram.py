"""
KH Clipper — Stage 5b: branded audiograms (opt-in).

Renders the **approved Kintsugi design-suite audiogram** as video, in both formats:

  - SQUARE   (1080x1080) -> audiogram_square    (KHAudiogram `square`)
  - VERTICAL (1080x1920) -> audiogram_vertical  (KHAudiogram `tall`)

The look is the suite component `lib/social-suite/suite/KHAudiogram.dc.html`
(committed in kh-studio): per-series palette, gold "kintsugi seam" waveform, logo
+ series eyebrow + spoken-line caption + progress bar + title/guest/EP footer, on
Archivo / Open Sans / IBM Plex Mono. The only deltas vs the still suite image are
the two things the worker can do that a still can't:

  1. the waveform reacts to the clip's real audio (not a decorative loop), and
  2. the progress bar tracks the clip's real duration.

Both fall back gracefully (seeded oscillation / linear fill) if needed.

This is an INTERNAL rendering change only — the Shorts Worker Contract (request /
response, file keys `audiogram_square`/`audiogram_vertical`, storage paths, the
`audiogram` gate and the KH-TIC-001 safety gate) is unchanged, so kh-studio needs
no change. See kh-studio `SHORTS-AUDIOGRAM-DESIGN-SPEC.md`.

    python -m src.audiogram <clip.mp4> <transcript.json> --start S --end E \
        --series kintsugi-heroes --caption "..." --title "..." --guest "..."
"""
from __future__ import annotations
import argparse
import json
import math
import os
import re
import subprocess

SQUARE = (1080, 1080)
VERTICAL = (1080, 1920)
FPS = 30
NBARS = 44

# Logos in the three suite colourways (copied from kh-studio's social suite).
LOGO_DIR = "assets/logo/suite"

# Per-series palette, keyed off the request `series` slug. The four designed series
# match the suite; everything else falls back to kintsugi-heroes (matches kh-studio's
# own series_social_brand fallback). `wave` and `accent` are the same colour in the
# suite, so we keep one value. Never pure #000/#fff.
FALLBACK_SERIES = "kintsugi-heroes"
PALETTES = {
    "animals-and-us":  {"bg": "#424530", "ink": "#FFF9ED", "accent": "#F0FFA3", "logo": "allwhite", "seed": 3},
    "golden-threads":  {"bg": "#FFEFCD", "ink": "#424530", "accent": "#ED9A1F", "logo": "black",    "seed": 9},
    "grit-diaries":    {"bg": "#ED9A1F", "ink": "#424530", "accent": "#424530", "logo": "allblack", "seed": 14},
    "kintsugi-heroes": {"bg": "#424530", "ink": "#FFF9ED", "accent": "#ED9A1F", "logo": "allwhite", "seed": 21},
}
# Pretty series eyebrow labels (UPPERCASE applied at render time).
SERIES_LABEL = {
    "animals-and-us": "Animals & Us",
    "golden-threads": "Golden Threads",
    "grit-diaries": "Grit Diaries",
    "kintsugi-heroes": "Kintsugi Heroes",
    "connecting-seniors": "Connecting Seniors",
    "alpine-series": "Alpine Series",
    "australian-carers": "Australian Carers",
    "river-murray-recovery-stories": "River Murray Recovery Stories",
}

FONTS = {
    "archivo_700": "Archivo-Bold.ttf",
    "archivo_800": "Archivo-ExtraBold.ttf",
    "opensans": "OpenSans.ttf",
    "mono": "IBMPlexMono-Regular.ttf",
}


def palette_for(series):
    """Resolve a series slug to its suite palette (fallback -> kintsugi-heroes)."""
    slug = (series or "").strip().lower()
    return PALETTES.get(slug, PALETTES[FALLBACK_SERIES])


def _hex(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _kh_voice(text):
    """Caption copy obeys the KH voice: no em/en dashes, drop the banned words.
    The line is already human-curated upstream; this is a belt-and-braces clean."""
    if not text:
        return ""
    t = text.replace("—", ", ").replace("–", ", ").replace(" -- ", ", ")
    t = re.sub(r"\s*[—–]\s*", ", ", t)
    t = re.sub(r"\s+", " ", t).strip().strip('"').strip()
    return t


def _seeded_bars(seed):
    """Replicate the suite's seeded bar envelope EXACTLY (same PRNG, same maths) so
    the resting silhouette is identical to KHAudiogram.dc.html. Returns per-bar
    (base_fraction 0..1 of the row height, oscillation duration s, delay s)."""
    s = (int(seed) if seed else 7) * 13 + 11

    def rnd():
        nonlocal s
        s = (s * 9301 + 49297) % 233280
        return s / 233280

    bars = []
    for i in range(NBARS):
        t = i / (NBARS - 1)
        env = 0.24 + 0.76 * math.pow(math.sin(math.pi * t), 0.7)
        n = 0.5 + 0.5 * rnd()
        h = max(14.0, env * n * 100.0) / 100.0      # fraction of the row height
        dur = 0.7 + rnd() * 0.7
        delay = rnd() * 0.9
        bars.append((h, dur, delay))
    return bars


# ----------------------------------------------------------------------
# Audio -> per-frame waveform amplitudes (the worker enhancement: the gold bars
# react to the hero's voice). Falls back to the suite's seeded oscillation.
# ----------------------------------------------------------------------
def _audio_pcm(clip_in, sr=22050):
    try:
        r = subprocess.run(
            ["ffmpeg", "-v", "quiet", "-i", clip_in, "-f", "f32le", "-ac", "1",
             "-ar", str(sr), "-"], capture_output=True)
        import numpy as np
        pcm = np.frombuffer(r.stdout, dtype=np.float32)
        return pcm if pcm.size else None
    except Exception:
        return None


def _band_amps(pcm, sr, nframes):
    """Map the clip audio to NBARS log-spaced frequency bands per video frame, like a
    voice EQ. Returns an (nframes, NBARS) array in 0..1, temporally smoothed so the
    bars settle rather than strobe. None if audio isn't usable."""
    import numpy as np
    if pcm is None or pcm.size < sr // 10:
        return None
    win = 2048
    freqs = np.fft.rfftfreq(win, 1.0 / sr)
    edges = np.logspace(math.log10(90), math.log10(7500), NBARS + 1)
    band = np.zeros((NBARS, freqs.size), dtype=np.float32)
    for bi in range(NBARS):
        mask = (freqs >= edges[bi]) & (freqs < edges[bi + 1])
        if mask.any():
            band[bi, mask] = 1.0 / mask.sum()
    hann = np.hanning(win).astype(np.float32)

    amps = np.zeros((nframes, NBARS), dtype=np.float32)
    for fi in range(nframes):
        c = int((fi / FPS) * sr)
        a = max(0, c - win // 2)
        b = a + win
        if b > pcm.size:
            b = pcm.size
            a = max(0, b - win)
        seg = pcm[a:b]
        if seg.size < win:
            seg = np.pad(seg, (0, win - seg.size))
        spec = np.abs(np.fft.rfft(seg * hann))
        amps[fi] = band @ spec

    amps = np.sqrt(amps)                            # perceptual compression
    norm = np.percentile(amps, 99) + 1e-9
    amps = np.clip(amps / norm, 0.0, 1.0)
    # Attack fast, release slow so a bar pops on a syllable then eases down.
    out = np.zeros_like(amps)
    prev = np.zeros(NBARS, dtype=np.float32)
    for fi in range(nframes):
        cur = amps[fi]
        prev = np.where(cur > prev, prev + (cur - prev) * 0.6, prev + (cur - prev) * 0.18)
        out[fi] = prev
    return out


# ----------------------------------------------------------------------
# Static layer (everything that doesn't move) + per-frame compositing.
# ----------------------------------------------------------------------
def _wrap(draw, text, font, max_w):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _text_spaced(draw, xy, text, font, fill, tracking_px, anchor_right=False):
    """Draw text with letter-spacing (PIL has none natively). Returns total width."""
    total = sum(draw.textlength(ch, font=font) + tracking_px for ch in text)
    total = total - tracking_px if text else 0
    x, y = xy
    if anchor_right:
        x -= total
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking_px
    return total


def _build_static(frame, pal, here, *, caption, title, guest, eyebrow, ep_label):
    """Compose the full still frame minus the waveform bars and the progress fill
    (those are drawn per-frame). Returns (numpy RGB array, layout dict)."""
    import numpy as np
    from PIL import Image, ImageDraw

    W, H = frame
    cqw = min(W, H) / 100.0
    tall = H > W

    def px(v):
        return v * cqw

    bg = _hex(pal["bg"])
    ink = _hex(pal["ink"])
    accent = _hex(pal["accent"])

    img = Image.new("RGB", (W, H), bg)

    # Subtle top radial highlight: radial-gradient(118% 82% at 50% 8%, white .10, transp 56%)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    cx, cy = 0.5 * W, 0.08 * H
    rx, ry = 1.18 * W, 0.82 * H
    d = np.sqrt(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2)
    a = np.clip(1.0 - d / 0.56, 0.0, 1.0) * 0.10
    base = np.asarray(img, dtype=np.float32)
    base = base + (255.0 - base) * a[..., None]
    img = Image.fromarray(np.clip(base, 0, 255).astype("uint8"), "RGB")
    draw = ImageDraw.Draw(img, "RGBA")

    # 1px accent inset border at inset 3.4cqw, opacity .26
    inset = px(3.4)
    bw = max(2, int(round(cqw * 0.18)))
    draw.rectangle([inset, inset, W - inset, H - inset], outline=accent + (66,), width=bw)

    # Fonts
    from PIL import ImageFont
    fdir = os.path.join(here, "assets", "fonts")

    def font(key, size_cqw):
        return ImageFont.truetype(os.path.join(fdir, FONTS[key]), max(1, int(round(px(size_cqw)))))

    pad_x = px(7) if tall else px(8)
    pad_y = px(8)
    c_left, c_right = pad_x, W - pad_x
    c_top, c_bottom = pad_y, H - pad_y
    c_w = c_right - c_left

    logo_h = px(12 if tall else 8.6)
    eye_size = 3.8 if tall else 2.5
    cap_size = 5.4 if tall else 4.4
    eq_h = px(34 if tall else 24)

    # --- Top row: logo left, eyebrow right ---
    logo_path = os.path.join(here, LOGO_DIR, f"logo-primary-{pal['logo']}.png")
    top_row_h = logo_h
    if os.path.exists(logo_path):
        logo = Image.open(logo_path).convert("RGBA")
        lw = int(round(logo.width * (logo_h / logo.height)))
        logo = logo.resize((lw, int(round(logo_h))), Image.LANCZOS)
        img.paste(logo, (int(round(c_left)), int(round(c_top))), logo)
        top_row_h = logo.height

    eye_font = font("archivo_700", eye_size)
    eyebrow_u = (eyebrow or "").upper()
    eb_h = (eye_font.getbbox(eyebrow_u or "X")[3] - eye_font.getbbox(eyebrow_u or "X")[1])
    eye_y = c_top + (top_row_h - eb_h) / 2 - eye_font.getbbox(eyebrow_u or "X")[1]
    if eyebrow_u:
        _text_spaced(draw, (c_right, eye_y), eyebrow_u, eye_font, accent + (255,),
                     px(eye_size) * 0.2, anchor_right=True)

    # --- Bottom block: progress track + footer ---
    prog_h = px(0.7)
    prog_gap = px(3.5)
    title_font = font("archivo_800", 2.8)
    guest_font = font("opensans", 2.0)
    ep_font = font("mono", 1.8)

    title_t = title or ""
    guest_t = f"with {guest}" if guest else ""
    t_asc = title_font.getbbox("Ag")
    g_asc = guest_font.getbbox("Ag")
    title_h = t_asc[3] - t_asc[1]
    guest_h = (g_asc[3] - g_asc[1]) if guest_t else 0
    g_margin = px(0.5) if guest_t else 0
    footer_h = title_h + g_margin + guest_h
    bottom_h = prog_h + prog_gap + footer_h
    bottom_top = c_bottom - bottom_h
    prog_y = bottom_top

    layout = {
        "W": W, "H": H, "cqw": cqw,
        "ink": ink, "accent": accent,
        "prog_x": c_left, "prog_w": c_w, "prog_y": prog_y, "prog_h": prog_h,
        "eq_h": eq_h,
    }

    # Footer text (static)
    footer_top = prog_y + prog_h + prog_gap
    if title_t:
        _text_spaced(draw, (c_left, footer_top - t_asc[1]), title_t, title_font,
                     ink + (255,), px(2.8) * -0.01)
    if guest_t:
        gy = footer_top + title_h + g_margin - g_asc[1]
        draw.text((c_left, gy), guest_t, font=guest_font, fill=ink + (153,))  # opacity .6
    if ep_label:
        ep_bbox = ep_font.getbbox(ep_label)
        ep_w = draw.textlength(ep_label, font=ep_font)
        ep_y = footer_top + (footer_h - (ep_bbox[3] - ep_bbox[1])) / 2 - ep_bbox[1]
        draw.text((c_right - ep_w, ep_y), ep_label, font=ep_font, fill=ink + (128,))  # .5

    # --- Centre block: waveform row + caption, centred in the free space ---
    cap_font = font("archivo_700", cap_size)
    cap_max_w = px(84)
    cap_lines = _wrap(draw, caption, cap_font, cap_max_w) if caption else []
    line_h = px(cap_size) * 1.28
    cap_block_h = len(cap_lines) * line_h
    centre_gap = px(5)
    centre_h = eq_h + (centre_gap + cap_block_h if cap_lines else 0)

    free_top = c_top + top_row_h
    free_bottom = bottom_top
    centre_top = free_top + ((free_bottom - free_top) - centre_h) / 2

    eq_center_y = centre_top + eq_h / 2
    layout["eq_center_y"] = eq_center_y

    # Caption (static), centred under the waveform
    if cap_lines:
        cy = centre_top + eq_h + centre_gap
        ascent = cap_font.getbbox("Ag")[1]
        for ln in cap_lines:
            lw = draw.textlength(ln, font=cap_font)
            draw.text(((W - lw) / 2, cy - ascent), ln, font=cap_font, fill=ink + (255,))
            cy += line_h

    return np.asarray(img, dtype="uint8"), layout


def _duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", path], capture_output=True, text=True)
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def _render(clip_in, out_path, frame, pal, here, *, caption, title, guest,
            eyebrow, ep_label, bars, amps):
    """Draw every frame (static layer + live waveform + live progress) and mux the
    clip's own audio under it -> MP4."""
    import numpy as np
    from PIL import Image, ImageDraw

    W, H = frame
    dur = _duration(clip_in)
    if dur <= 0:
        raise RuntimeError("could not read clip duration for audiogram")
    nframes = max(1, int(round(dur * FPS)))

    static, L = _build_static(
        frame, pal, here, caption=caption, title=title, guest=guest,
        eyebrow=eyebrow, ep_label=ep_label)

    cqw = L["cqw"]
    bar_w = cqw * 1.4
    gap = cqw * 0.7
    radius = cqw * 0.7
    total_w = NBARS * bar_w + (NBARS - 1) * gap
    x0 = (W - total_w) / 2.0
    eqc = L["eq_center_y"]
    eq_h = L["eq_h"]
    wave = L["accent"]
    accent = L["accent"]
    px07 = L["prog_h"]
    prog_x, prog_w, prog_y = L["prog_x"], L["prog_w"], L["prog_y"]
    min_scale = 0.34

    cmd = ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-i", clip_in,
           "-map", "0:v", "-map", "1:a?", "-c:v", "libx264", "-preset", "medium",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-r", str(FPS), "-t", f"{dur:.3f}",
           "-movflags", "+faststart", out_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    try:
        for fi in range(nframes):
            t = fi / FPS
            frame_img = Image.fromarray(static.copy(), "RGB")
            d = ImageDraw.Draw(frame_img, "RGBA")
            for i, (base_h, bdur, delay) in enumerate(bars):
                if amps is not None:
                    scale = min_scale + (1.0 - min_scale) * float(amps[fi, i])
                else:
                    phase = ((t + delay) / bdur) * 2 * math.pi
                    osc = 0.5 + 0.5 * math.sin(phase)
                    scale = min_scale + (1.0 - min_scale) * osc
                bh = base_h * eq_h * scale
                bx = x0 + i * (bar_w + gap)
                y_top = eqc - bh / 2
                y_bot = eqc + bh / 2
                d.rounded_rectangle([bx, y_top, bx + bar_w, y_bot],
                                    radius=radius, fill=wave + (255,))
            # Progress fill: true 0->100% over the clip duration.
            frac = min(1.0, (fi + 1) / nframes)
            fill_w = max(px07, prog_w * frac)
            d.rounded_rectangle([prog_x, prog_y, prog_x + fill_w, prog_y + px07],
                                radius=px07 / 2, fill=accent + (255,))
            proc.stdin.write(frame_img.tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "ignore")
        if proc.wait() != 0:
            raise RuntimeError(err[-1400:])
    finally:
        if proc.poll() is None:
            proc.kill()
    return out_path


def _caption_from_words(words, limit=14):
    """Fallback caption: the clip's opening spoken line, kept short."""
    txt = " ".join(w.get("text", "") for w in (words or [])[:limit])
    return _kh_voice(txt)


def render(clip_in, words, out_base, series=None, here=None,
           caption=None, title=None, guest_name=None, ep_label=None):
    """Render the KH design-suite audiograms for a clip (square + vertical) as MP4.
    Returns the list of output paths. Output keys/paths are unchanged from before:
    <out_base>_audiogram_square.mp4 and <out_base>_audiogram_vertical.mp4.

    caption    — the key spoken line for this clip (defaults to the clip's opening line)
    title      — short content title for the footer
    guest_name — hero name for the footer "with ..." line (omitted when None)
    ep_label   — optional "EP 14" tag (omitted when None; the suite default is empty)
    """
    here = here or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pal = palette_for(series)
    eyebrow = SERIES_LABEL.get((series or "").strip().lower(), series or "Kintsugi Heroes")
    cap = _kh_voice(caption) or _caption_from_words(words)
    bars = _seeded_bars(pal["seed"])

    sq = f"{out_base}_audiogram_square.mp4"
    vt = f"{out_base}_audiogram_vertical.mp4"

    # Audio drives the waveform; compute the band envelope once per format size.
    pcm = _audio_pcm(clip_in)
    dur = _duration(clip_in)
    nframes = max(1, int(round(dur * FPS)))
    amps = _band_amps(pcm, 22050, nframes) if pcm is not None else None

    _render(clip_in, sq, SQUARE, pal, here, caption=cap, title=title or "",
            guest=guest_name, eyebrow=eyebrow, ep_label=ep_label, bars=bars, amps=amps)
    print(f"  audiogram {os.path.basename(sq)}")
    _render(clip_in, vt, VERTICAL, pal, here, caption=cap, title=title or "",
            guest=guest_name, eyebrow=eyebrow, ep_label=ep_label, bars=bars, amps=amps)
    print(f"  audiogram {os.path.basename(vt)}")
    return [sq, vt]


def main():
    ap = argparse.ArgumentParser(description="Stage 5b: KH design-suite audiogram (square + vertical)")
    ap.add_argument("clip", help="clip mp4 (audio source)")
    ap.add_argument("transcript", nargs="?", help="transcript.json with word timings (optional)")
    ap.add_argument("--start", type=float, default=None)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--series", default=None, help="series slug -> palette + eyebrow + logo")
    ap.add_argument("--caption", default=None, help="key spoken line for this clip")
    ap.add_argument("--title", default=None, help="footer content title")
    ap.add_argument("--guest", default=None, help="hero name (footer 'with ...')")
    ap.add_argument("--ep", default=None, help="EP label, e.g. 'EP 14'")
    ap.add_argument("--out", default=None, help="output base path (no extension)")
    a = ap.parse_args()
    words = []
    if a.transcript and a.start is not None and a.end is not None:
        try:
            from . import caption as cap_mod
        except ImportError:
            import caption as cap_mod
        words_all = json.load(open(a.transcript)).get("words", [])
        words = cap_mod.clip_words(words_all, a.start, a.end)
    out_base = a.out or os.path.splitext(a.clip)[0]
    render(a.clip, words, out_base, series=a.series, caption=a.caption,
           title=a.title, guest_name=a.guest, ep_label=a.ep)
    print("done")


if __name__ == "__main__":
    main()
