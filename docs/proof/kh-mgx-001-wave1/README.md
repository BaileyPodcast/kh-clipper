# KH-MGX-001 Wave 1 — visual proof

Frames pulled from real `ffmpeg`/`libass` renders through the unmodified
`src/caption.finish()` pipeline (synthetic sources, since this environment has
no real KH footage — the caption/CTA/logo/zoom pipeline itself is exactly
production code, nothing mocked).

- `standard-popin-and-highlight.png` — standard preset, mid-clip. "not" is the
  active word (gold, mid pop-in scale); "broken" is the clip's `highlight_word`
  (gold, oversized) staying emphasised even though it isn't the active word.
- `calm-preset-flat.png` — same words/highlight, `safety="review"` -> CALM
  preset. The active word ("broken") still turns gold but never scales up —
  no pop, no highlight oversizing, per KH-TIC-001.
- `lowface-raised-band.png` — a simulated low face band
  (`{"top": 0.30, "bottom": 0.72}`) raises the caption block from the default
  380px margin to 620px, clear of where a low-sitting face would be.
- `punch-in-start.png` / `punch-in-end.png` — first vs last frame of a 6s
  textured test clip with the punch-in enabled. The colour bars/checkerboard
  visibly grow (~100% -> ~104%) while the CTA text overlay on top stays crisp
  and unscaled, confirming the zoom only touches the base footage.

Regenerate with the script pasted into the PR description, or ask for it.
