"""
KH Clipper — Wave 2 (KH-MGX-001): the Remotion premium render bridge.

The kinetic caption_style renders through render/render-cli.mjs (a Node +
Remotion project) instead of libass. This module is the ONLY thing Python
calls to reach it — clipper.py branches here at the finish stage exactly
where it would otherwise call src.caption.finish() for the classic style.

Input contract (per the brief): the REFRAMED, UNBURNED 9:16 clip + clip-
relative word timings + brand.json (kept in sync with src/brand.py by
src/export_brand.py — never hand-edited).

    python -m src.kinetic <clip.mp4> <transcript.json> --start 850.8 --end 859.4
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import tempfile
import time

from src import export_brand

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENDER_DIR = os.path.join(_HERE, "render")
RENDER_CLI = os.path.join(RENDER_DIR, "render-cli.mjs")


def available() -> bool:
    """The Remotion render layer needs `render/` set up (npm install run) —
    this is the same guard-rail pattern as src/endscreen.py's `available()`:
    a missing optional dependency degrades gracefully, never crashes a job."""
    return os.path.exists(RENDER_CLI) and os.path.isdir(os.path.join(RENDER_DIR, "node_modules"))


def _probe(path, entries, of="csv=p=0"):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", entries, "-of", of, path],
        capture_output=True, text=True,
    )
    return r.stdout.strip()


def _duration(path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True,
    )
    return float(json.loads(r.stdout)["format"]["duration"])


def _fps(path) -> float:
    raw = _probe(path, "stream=r_frame_rate")
    try:
        num, _, den = raw.partition("/")
        fps = float(num) / float(den) if den else float(num)
        return fps if fps > 0 else 30.0
    except (ValueError, ZeroDivisionError):
        return 30.0


def finish(clip_in, words, out_base, banner=None, highlight_word=None, safety="ok",
           faceband=None, frame=(1080, 1920), variants=("shorts", "universal"),
           end_screen_cta=True):
    """Render the KH Kinetic template. Mirrors src.caption.finish()'s call
    shape and return value (a list of written file paths) so clipper.py can
    branch between the two with no other change downstream.

    `end_screen_cta` (Wave 2 — KH End Screen, default ON): an animated CTA
    overlay on the clip's own final seconds — arrows-at-native-UI for the
    "shorts" variant, branded text/handle for "universal". Default ON so
    `caption_style="kinetic"` stays at parity with classic's own always-on
    CTA (src/cta.py) rather than being a downgrade; pass False to opt a
    single render out.

    With `end_screen_cta` on, "shorts" and "universal" are genuinely
    different renders now (arrows vs branded text/handle) — each requested
    variant gets its own render. With it off every variant is still
    byte-identical (no CTA difference yet, same as pre-End-Screen v1), so
    this keeps the original render-once-and-copy cost optimisation for that
    case rather than paying for N identical renders.

    Raises RuntimeError on failure (never partially writes a "finished" file
    the caller would upload) — mirrors caption.finish()'s own contract."""
    if not available():
        raise RuntimeError(
            "kinetic render unavailable — render/node_modules missing (run `npm install` in render/)"
        )

    brand_path = export_brand.export_brand(os.path.join(RENDER_DIR, "brand.json"))
    dur = _duration(clip_in)
    fps = _fps(clip_in)

    with tempfile.TemporaryDirectory() as tmp:
        words_path = os.path.join(tmp, "words.json")
        with open(words_path, "w") as f:
            json.dump(words, f)

        faceband_path = None
        if faceband:
            faceband_path = os.path.join(tmp, "faceband.json")
            with open(faceband_path, "w") as f:
                json.dump(faceband, f)

        base_cmd = [
            "node", RENDER_CLI,
            "--video", clip_in,
            "--words", words_path,
            "--brand", brand_path,
            "--duration", str(dur),
            "--fps", str(fps),
            "--width", str(frame[0]),
            "--height", str(frame[1]),
            "--safety", safety or "ok",
        ]
        if highlight_word:
            base_cmd += ["--highlight", highlight_word]
        if banner:
            base_cmd += ["--banner", banner]
        if faceband_path:
            base_cmd += ["--faceband", faceband_path]
        if not end_screen_cta:
            base_cmd += ["--no-end-screen-cta"]
        env = dict(os.environ)
        browser = os.environ.get("REMOTION_BROWSER_EXECUTABLE")
        if browser:
            env["REMOTION_BROWSER_EXECUTABLE"] = browser

        # end_screen_cta on -> shorts/universal genuinely differ, so each
        # variant renders separately. off -> still identical either way, so
        # render ONCE (the original v1 cost optimisation) and copy below.
        render_variants = list(variants) if end_screen_cta else [variants[0]]
        rendered_paths = {}
        total_render_s = 0.0
        for variant in render_variants:
            rendered = os.path.join(tmp, f"kinetic_{variant}.mp4")
            cmd = base_cmd + ["--out", rendered, "--variant", variant]
            t0 = time.time()
            r = subprocess.run(cmd, capture_output=True, text=True, cwd=RENDER_DIR, env=env)
            if r.returncode != 0:
                raise RuntimeError(r.stderr[-1600:] or r.stdout[-1600:])
            total_render_s += time.time() - t0
            rendered_paths[variant] = rendered

        outs = []
        for variant in variants:
            src_variant = variant if end_screen_cta else render_variants[0]
            out = f"{out_base}_{variant}.mp4"
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            with open(rendered_paths[src_variant], "rb") as src_f, open(out, "wb") as dst_f:
                dst_f.write(src_f.read())
            outs.append(out)
        # v1/v2 render-cost note (Wave 2 acceptance criterion) — wall-clock
        # render time for the real render(s) only, never per copied variant.
        print(f"  finished {len(outs)} kinetic export(s) from {os.path.basename(out_base)} "
              f"({total_render_s:.1f}s render, {len(rendered_paths)} unique render(s))")
    return outs


def main():
    ap = argparse.ArgumentParser(description="Wave 2: KH Kinetic render (Remotion)")
    ap.add_argument("clip", help="reframed 9:16 clip")
    ap.add_argument("transcript", help="transcript.json with word timings")
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--end", type=float, required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--highlight", default=None)
    ap.add_argument("--banner", default=None)
    ap.add_argument("--safety", default="ok")
    ap.add_argument("--no-end-screen-cta", action="store_false", dest="end_screen_cta",
                    default=True, help="Wave 2: opt this render out of the tail-window CTA overlay")
    args = ap.parse_args()
    from src.caption import clip_words
    words_all = json.load(open(args.transcript)).get("words", [])
    words = clip_words(words_all, args.start, args.end)
    out_base = args.out or os.path.splitext(args.clip)[0]
    finish(args.clip, words, out_base, highlight_word=args.highlight,
           banner=args.banner, safety=args.safety, end_screen_cta=args.end_screen_cta)
    print("done")


if __name__ == "__main__":
    main()
