"""
KH Clipper — Wave 2 (KH-MGX-001): the KH Audiogram v2 Remotion bridge.

Replaces/augments the Pillow frame-by-frame audiogram (src/audiogram.py) for
promo video (ties into KH-VRL-001) with a Remotion render of the SAME
KH design-suite audiogram look (source of truth: kh-studio's
`lib/social-suite/suite/KHAudiogram.dc.html`, per
SHORTS-AUDIOGRAM-DESIGN-SPEC.md), adding real animated elements Pillow's
independent per-frame compositing can't cleanly do: a spring-eased,
staggered waveform-bar entrance, cross-fading timed caption transitions,
and a data-driven progress fill with a subtle completion glow.

This module deliberately REUSES src/audiogram.py's audio-envelope,
per-series palette and transcript-grouping machinery directly (the FFT-based
`_band_amps()` band analysis, the seeded `_seeded_bars()` envelope shape,
`resolve_brand()`/`palette_for()`, `group_caption_lines()`) rather than
re-implementing any of it — Python does the audio/data work, React only
renders, the same division of labour src/kinetic.py already established for
word timings. This is the ONLY thing Python calls to reach the Remotion
audiogram-v2 composition; it mirrors src.kinetic.finish()'s call
shape/return value so a caller can branch between templates identically.

    python -m src.audiogram_v2 <clip.mp4> [transcript.json] --start S --end E \\
        --series kintsugi-heroes --caption "..." --title "..." --guest "..." \\
        [--format landscape|square|vertical] [--safety ok|review] [--out <out_base>]
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import tempfile
import time

from src import export_brand
from src.audiogram import (
    LOGO_DIR,
    _audio_pcm,
    _band_amps,
    _duration,
    _kh_voice,
    _seeded_bars,
    group_caption_lines,
    resolve_brand,
)

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENDER_DIR = os.path.join(_HERE, "render")
RENDER_CLI = os.path.join(RENDER_DIR, "render-cli.mjs")

# Matches src/audiogram.py's own module-level FPS exactly. `_band_amps()`
# (reused unchanged below) indexes frames against that constant internally
# via `c = int((fi / FPS) * sr)`, so a real-audio envelope computed at any
# OTHER rate would silently desync from the frames it is meant to drive —
# the render is therefore always produced at this rate too, same as the
# Pillow version's own hardcoded-FPS design.
FPS = 30

LANDSCAPE = (1920, 1080)
VERTICAL = (1080, 1920)
SQUARE = (1080, 1080)
FORMAT_TO_FRAME = {"landscape": LANDSCAPE, "vertical": VERTICAL, "square": SQUARE}


def available() -> bool:
    """The Remotion render layer needs `render/` set up (npm install run) —
    same guard-rail pattern as src/kinetic.py's available(): a missing
    optional dependency degrades gracefully, never crashes a job."""
    return os.path.exists(RENDER_CLI) and os.path.isdir(os.path.join(RENDER_DIR, "node_modules"))


def compute_envelope(clip_in, seed):
    """Real per-frame audio envelope (an (nframes, NBARS) numpy array, 0..1)
    plus the seeded bar envelope every bar's silhouette height is anchored
    to — reused, unmodified, from src/audiogram.py. `seed_bars` shapes the
    waveform's resting arc in BOTH the audio-driven and decorative-
    oscillation-fallback cases, exactly like src/audiogram.py's own
    `_render()` (`bh = base_h * eq_h * scale`, base_h independent of amps).

    Returns (amps_or_none, seed_bars, nframes, duration_sec).
    """
    dur = _duration(clip_in)
    nframes = max(1, int(round(dur * FPS)))
    pcm = _audio_pcm(clip_in)
    amps = _band_amps(pcm, 22050, nframes) if pcm is not None else None
    seed_bars = _seeded_bars(seed)
    return amps, seed_bars, nframes, dur


def _logo_file(colourway: str) -> str:
    """Relative path (repo-root-relative, matching render-cli.mjs's staging
    convention) to the per-series logo colourway PNG, mirroring
    src/audiogram.py's own `os.path.join(here, LOGO_DIR, ...)` lookup."""
    return os.path.join(LOGO_DIR, f"logo-primary-{colourway}.png")


def build_props(clip_in, words, *, series, brand_override, caption, title,
                 guest_name, ep_label, width, height, fps, safety):
    """Resolve everything render-cli.mjs's `audiogram-v2` mode needs beyond
    brand.json + the staged clip: the per-series palette + text tokens
    (resolve_brand — identical fallback rules to the Pillow path), the
    computed envelope, and the caption (static line, or timed chunks when
    a transcript window was supplied). Mirrors src/audiogram.py's
    render()/render_landscape() text resolution exactly, so the same input
    produces the same words on screen either way.

    Returns (props_dict, notes, duration_sec). `notes` carries any brand
    override fallback warnings (see resolve_brand), same as the Pillow path.
    """
    pal, texts, notes = resolve_brand(series, brand_override)
    eyebrow = texts["eyebrow"]
    footer_title = title or texts["display_name"] or ""
    cap = (_kh_voice(caption) or _kh_voice(footer_title)
           or _kh_voice(texts["tagline"] or "")) or None

    amps, seed_bars, nframes, dur = compute_envelope(clip_in, pal["seed"])

    # Timed caption chunks take priority over the static line, mirroring
    # audiogram.py's own timed_captions branch (`_build_static` never draws
    # the static caption once `timed_captions` is true) — never send both
    # to the template, one clean either/or contract.
    timed = None
    if words:
        timed = [c for c in group_caption_lines(words) if _kh_voice(c["text"])]
        timed = timed or None
    if timed:
        cap = None

    props = {
        "title": footer_title or None,
        "guestName": guest_name,
        "eyebrow": eyebrow,
        "epLabel": ep_label,
        "caption": cap,
        "timedLines": timed,
        "amps": amps.tolist() if amps is not None else None,
        "seedBars": [{"h": h, "dur": d, "delay": dl} for h, d, dl in seed_bars],
        "palette": {"bg": pal["bg"], "ink": pal["ink"], "accent": pal["accent"]},
        "logoFile": _logo_file(pal["logo"]),
        "safety": safety or "ok",
        "width": width,
        "height": height,
        "fps": fps,
        "durationInFrames": nframes,
    }
    return props, notes, dur


def finish(clip_in, words, out_base, *, series=None, brand=None, caption=None,
           title=None, guest_name=None, ep_label=None, safety="ok",
           frame=LANDSCAPE, variants=("promo",)):
    """Render the KH Audiogram v2 template. Mirrors src.kinetic.finish()'s
    call shape and return value (a list of written file paths), so a caller
    can dispatch between the two Wave 2 templates identically.

    Raises RuntimeError on failure (never partially writes a "finished" file
    the caller would upload) — mirrors kinetic.finish()'s contract exactly.
    """
    if not available():
        raise RuntimeError(
            "audiogram v2 render unavailable — render/node_modules missing (run `npm install` in render/)"
        )

    brand_path = export_brand.export_brand(os.path.join(RENDER_DIR, "brand.json"))
    width, height = frame

    props, notes, _dur = build_props(
        clip_in, words, series=series, brand_override=brand, caption=caption,
        title=title, guest_name=guest_name, ep_label=ep_label, width=width,
        height=height, fps=FPS, safety=safety,
    )

    with tempfile.TemporaryDirectory() as tmp:
        props_path = os.path.join(tmp, "props.json")
        with open(props_path, "w") as f:
            json.dump(props, f)

        rendered = os.path.join(tmp, "audiogram.mp4")
        cmd = [
            "node", RENDER_CLI, "--composition", "audiogram-v2",
            "--video", clip_in,
            "--brand", brand_path,
            "--props", props_path,
            "--out", rendered,
        ]
        env = dict(os.environ)
        browser = os.environ.get("REMOTION_BROWSER_EXECUTABLE")
        if browser:
            env["REMOTION_BROWSER_EXECUTABLE"] = browser

        t0 = time.time()
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=RENDER_DIR, env=env)
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-1600:] or r.stdout[-1600:])
        render_s = time.time() - t0

        outs = []
        for variant in variants:
            out = f"{out_base}_{variant}.mp4"
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            with open(rendered, "rb") as src_f, open(out, "wb") as dst_f:
                dst_f.write(src_f.read())
            outs.append(out)
        note_suffix = f" [{'; '.join(notes)}]" if notes else ""
        print(f"  finished {len(outs)} audiogram v2 export(s) from {os.path.basename(out_base)} "
              f"({render_s:.1f}s render){note_suffix}")
    return outs


def main():
    ap = argparse.ArgumentParser(description="Wave 2: KH Audiogram v2 render (Remotion)")
    ap.add_argument("clip", help="clip mp4 (audio source)")
    ap.add_argument("transcript", nargs="?", help="transcript.json with word timings (optional)")
    ap.add_argument("--start", type=float, default=None)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--series", default=None, help="series slug -> palette + eyebrow + logo")
    ap.add_argument("--caption", default=None, help="key spoken line for this clip")
    ap.add_argument("--title", default=None, help="footer content title")
    ap.add_argument("--guest", default=None, help="hero name (footer 'with ...')")
    ap.add_argument("--ep", default=None, help="EP label, e.g. 'EP 14'")
    ap.add_argument("--format", default="landscape", choices=list(FORMAT_TO_FRAME.keys()))
    ap.add_argument("--safety", default="ok")
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
    finish(a.clip, words, out_base, series=a.series, caption=a.caption,
           title=a.title, guest_name=a.guest, ep_label=a.ep, safety=a.safety,
           frame=FORMAT_TO_FRAME[a.format])
    print("done")


if __name__ == "__main__":
    main()
