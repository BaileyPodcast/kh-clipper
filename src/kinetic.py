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
           faceband=None, frame=(1080, 1920), variants=("shorts", "universal")):
    """Render the KH Kinetic template. Mirrors src.caption.finish()'s call
    shape and return value (a list of written file paths) so clipper.py can
    branch between the two with no other change downstream.

    v1 (this PR) has no per-variant CTA differences yet (Remotion CTA end
    cards, the arrows-vs-branded-text split libass's cta.py does, are a later
    Wave 2 template) — so `_shorts`/`_universal` would be byte-identical.
    Rendering the same composition twice would double render cost for no
    difference, so this renders ONCE and copies the result to every requested
    variant name, keeping the same multi-file contract without paying for it
    twice. Flagged, not silently hidden: when the Wave 2 CTA template lands,
    this becomes a real per-variant render like caption.finish()'s.

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

        rendered = os.path.join(tmp, "kinetic.mp4")
        cmd = [
            "node", RENDER_CLI,
            "--video", clip_in,
            "--words", words_path,
            "--brand", brand_path,
            "--out", rendered,
            "--duration", str(dur),
            "--fps", str(fps),
            "--width", str(frame[0]),
            "--height", str(frame[1]),
            "--safety", safety or "ok",
        ]
        if highlight_word:
            cmd += ["--highlight", highlight_word]
        if banner:
            cmd += ["--banner", banner]
        if faceband_path:
            cmd += ["--faceband", faceband_path]
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
        # v1 render-cost note (Wave 2 acceptance criterion) — wall-clock render
        # time for the one real render, not per copied variant.
        print(f"  finished {len(outs)} kinetic export(s) from {os.path.basename(out_base)} "
              f"({render_s:.1f}s render)")
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
    args = ap.parse_args()
    from src.caption import clip_words
    words_all = json.load(open(args.transcript)).get("words", [])
    words = clip_words(words_all, args.start, args.end)
    out_base = args.out or os.path.splitext(args.clip)[0]
    finish(args.clip, words, out_base, highlight_word=args.highlight,
           banner=args.banner, safety=args.safety)
    print("done")


if __name__ == "__main__":
    main()
