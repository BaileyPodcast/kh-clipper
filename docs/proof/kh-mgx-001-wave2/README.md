# KH-MGX-001 Wave 2 — visual proof (KH Kinetic template)

Frames pulled from REAL renders through the unmodified `render-cli.mjs` ->
`@remotion/bundler` -> `@remotion/renderer` pipeline (a real, locally-run
headless Chromium, not mocked), on a synthetic test source (this environment
has no real KH footage or network route to fetch one — the render pipeline
itself, including the caption/highlight/CALM/band/font logic, is exactly
production code).

- `kinetic-popin-and-highlight.png` — standard preset, first caption page.
  "not" is the active word (gold, spring pop-in); "broken" is the clip's
  `highlightWord` (gold, oversized), rendered with no overlap into the next
  word — a real overlap bug (CSS `transform: scale()` doesn't reflow layout)
  found on the first render and fixed with a flex+gap layout; see the PR body.
- `kinetic-second-page.png` — the SECOND caption page ("you are still here"),
  confirming the createTikTokStyleCaptions page grouping + the
  `maxWordsPerLine` re-chunk correctly split this from the first page instead
  of fusing all 8 words into one wall of text (also a real bug found and
  fixed — the initial 1200ms combine window was far too loose).
- `kinetic-calm-preset.png` — same words/highlight, `safety="review"` -> CALM.
  The active word ("not") still turns gold but never scales; "broken" (the
  highlight word) is NOT oversized — matches Wave 1's CALM guard exactly.
- `kinetic-lowface-band.png` — a simulated low face band
  (`{"top": 0.30, "bottom": 0.72}`) raises the caption block from the default
  380px bottom margin to 620px, same rule and same brand.json values as
  Wave 1's libass path.

Regenerate: see `render/README.md` "Try it".
