# KH Clipper render/ — the Remotion premium render layer (Wave 2, KH-MGX-001)

A second, selectable render path for Shorts (`caption_style="kinetic"`,
alongside the default `"classic"` libass path in `src/caption.py`): sprung
word-by-word captions, the clip's highlight word oversized in gold, the same
face-aware placement and CALM trauma-informed preset as Wave 1 — everything
libass can't do (spring physics, proper web typography, animated end screens
and quote cards in later waves).

**v1 of this PR ships ONE template: KH Kinetic.** The other Wave 2 templates
(KH Quote Card intro, KH End Screen, KH Audiogram v2) are explicit follow-ups,
each its own PR, per the brief's own phased order.

## License position (verified 2026-08-05, re-verify at build time)

Remotion's license (`remotion-dev/remotion/LICENSE.md`) grants a **Free
License** to, among others, "a non-profit or not-for-profit organization" —
no employee-count or revenue cap for that category (the 3-employee cap in the
license applies only to *for-profit* entities). Kintsugi Heroes is a genuine
NFP, so it qualifies at no cost, for both non-commercial and commercial use of
the software "for the purpose of creating videos and images". The one
restriction that matters here: **we cannot copy/modify Remotion's own code to
sell, relicense or sublicense a derivative** — irrelevant to what this
directory does (it consumes Remotion as a normal npm dependency to render our
own branded videos, nothing more).

Re-verify this at build time (license terms can change) before relying on it
for a new Wave 2 template or a licensing-adjacent decision.

## How it fits together

```
src/brand.py  --(python -m src.export_brand)-->  render/brand.json
                                                        |
reframed 9:16 clip (unburned) + word timings JSON  -->  render-cli.mjs
                                                        |
                                          @remotion/bundler + @remotion/renderer
                                                        |
                                                 finished kinetic MP4
```

- **`src/brand.py` stays the single source of truth.** `python -m
  src.export_brand` is the ONLY thing that reads it and writes
  `render/brand.json` — React never hardcodes a colour, font or timing.
  `render/brand.json` is gitignored (generated, not committed).
- **`src/kinetic.py`** is the only thing Python calls to reach this layer —
  mirrors `src.caption.finish()`'s call shape/return value so
  `clipper.py`'s `_finish()` can branch between "classic" and "kinetic" with
  no other change downstream. Degrades gracefully (`kinetic.available()`)
  and `clipper.py` falls back to classic on any kinetic failure — a clip is
  never lost to a Remotion-side error.
- **`render-cli.mjs`** is the Node entry point `src/kinetic.py` shells out to:
  bundles `src/Root.tsx`, resolves the `KHKinetic` composition, renders it via
  `@remotion/renderer`'s `renderMedia`, muxes the clip's own real audio track
  (`enforceAudioTrack: true`). The source clip and the two KH font files are
  staged into a temp `publicDir` per render (Remotion serves local assets via
  `staticFile()`, not arbitrary filesystem paths) and cleaned up after.
- **`src/KHKinetic.tsx`** is the template: word timings -> `@remotion/captions`'
  `createTikTokStyleCaptions()` -> pages, re-chunked to
  `brand.caption.maxWordsPerLine` (mirrors Wave 1's fixed line length — the
  TikTok-style pager alone groups by time proximity only, with no cap, so a
  page could otherwise grow into a wall of text). Each active word pops in via
  `spring()` from `popFromScale` to its target scale over `popMs`; the clip's
  `highlightWord` stays gold + oversized whenever it's visible, active or not.
  The CALM preset (any `safety != "ok"`, same rule as Wave 1) renders colour
  changes only — no pop, no scale, no highlight oversizing.

## Setup

```bash
cd render
npm ci                          # or `npm install` if you're changing deps
python -m src.export_brand      # from the repo root — writes render/brand.json
```

Rendering needs a Chromium-family browser. Remotion downloads and caches its
own Chrome Headless Shell on first use by default — nothing to install. To
point it at an already-installed browser instead (faster cold start, e.g. in
this dev sandbox or a pre-baked image), set:

```bash
export REMOTION_BROWSER_EXECUTABLE=/path/to/chrome-or-chromium
```

**A known Chrome/Chromium build gotcha, hit and worked around while building
this**: modern Chrome/Chromium builds removed "old headless" mode, which some
Puppeteer/Remotion code paths still request by default against certain
browser builds — pointing `REMOTION_BROWSER_EXECUTABLE` at a **headless
shell** binary (e.g. Playwright's `chromium_headless_shell-*` build,
distinct from its regular `chromium` build) sidesteps it. If you hit `Failed
to launch the browser process! Old Headless mode has been removed...`, that's
the fix.

## Try it

```bash
node render-cli.mjs \
  --video /path/to/reframed_9x16.mp4 \
  --words /path/to/words.json \
  --brand brand.json \
  --out /tmp/out.mp4 \
  --duration 6.0 --fps 30 \
  --highlight broken --banner "The moment everything changed" --safety ok
```

`words.json` is the same clip-relative `[{"text","start","end"}, ...]` shape
`src.caption.clip_words()` already produces — nothing new to build on the
transcript side.

## Modal image (worker/app.py)

Node 22 (via NodeSource, not whatever Debian's apt happens to ship) is added
to the SAME Modal image the rest of the worker runs in — one function, no
cross-function orchestration, "keep it boring" per the brief. `render/` is
copied into the image and `npm ci --omit=dev` runs at BUILD time, so a cold
worker start never waits on the npm registry. No browser is baked into the
image (Remotion downloads its own on first render, per above) — worth
revisiting if that first-render cold-start cost matters in practice; not
optimised in this PR.

**Not verified from this environment**: an actual `modal deploy` (no Modal
credentials in this sandbox) — the render pipeline itself is verified,
extensively, with real ffmpeg/Chromium renders (see the PR's proof set), but
the Modal image change (Node install, `npm ci` at build time, image size/cold
start) has not been deployed or exercised for real. Needs a real `modal
deploy` + a real job before `caption_style="kinetic"` is trusted in
production.
