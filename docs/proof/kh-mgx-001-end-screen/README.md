# KH-MGX-001 Wave 2 — visual proof (KH End Screen)

Frames pulled from REAL renders through the unmodified `render-cli.mjs` ->
`@remotion/bundler` -> `@remotion/renderer` pipeline (a real, locally-run
headless Chromium, not mocked), on the same synthetic test source used for
the Wave 2 KH Kinetic / Quote Card intro proofs (this environment has no
real KH footage or network route to fetch one — the render pipeline itself,
including the CTA/CALM/band logic, is exactly production code). Also
exercised end to end through the real Python bridge (`python -m src.kinetic`
and `src.kinetic.finish()` directly), not just `render-cli.mjs` alone.

The End Screen CTA is default ON (parity with classic's own always-on CTA,
`src/cta.py`) and is an OVERLAY on the clip's own final seconds
(`brand.animation.endScreen.windowSec`, 3.0s by default) — it never extends
the composition's total length, unlike the Quote Card intro (which prepends
and genuinely extends it).

## Real bug found and fixed before shipping

`es_before_fix_collision_bug.png` — the FIRST real render, standard preset,
shorts variant, mid-window. The three stacked CTA pills ("Don't forget to
subscribe" / "Listen to the full episode" / "Full episode in the linked
video below") sat at a hardcoded `bottom: 800` offset. Because this template
shows all three messages at once (see "Design decision" below) rather than
classic's one-line-at-a-time rotation, its footprint is taller than
classic's own CTA ever needs — and a 2-line caption page landing during the
end window pushed its own box high enough to collide with the arrows/bottom
of the CTA block. Fixed by anchoring the CTA block's position off the SAME
`bandForCaptions()` the captions themselves use (`brand.animation.
captionBands`), plus a fixed clearance (`CAPTION_CLEARANCE_PX = 550`) — so
the block always sits a consistent, safe distance above wherever the
captions actually render, in either the default or raised face-band, and
regardless of 1- or 2-line wrap. `es_shorts_arrows.png` (below) is the SAME
moment in the SAME clip, after the fix — no overlap, and the CTA block
correctly moved up (the caption band + clearance pushed it higher than the
old hardcoded value).

## Frames (after the fix)

- `es_shorts_arrows.png` — standard preset, "shorts" variant, mid-window.
  All three CTA lines visible, "subscribe" and "full" accented gold, three
  gold down-arrows pointing at the tuned `brand.cta.shortsTargets` pixel
  positions (the SAME positions `src/cta.py`'s classic CTA already uses) —
  no overlap with the caption page rendering underneath.
- `es_universal_handle.png` — standard preset, "universal" variant, mid-
  window. No arrows; the `@kintsugiheroes` handle renders as a second pill
  under "Don't forget to subscribe" instead — branded text only, matching
  classic's own shorts/universal split.
- `es_calm_fade_only.png` — CALM preset (`--safety review`), shorts variant,
  0.167s into the window (partway through the entrance fade). The subscribe
  pill and its arrow are both mid-fade with ZERO vertical drift — confirms
  the CTA overlay follows the same KH-TIC-001 fades-only rule as
  QuoteCardIntro and the caption pop-in, even though classic's own CTA
  (`src/cta.py`) has no CALM branch at all (see the PR body).
- `es_disabled_regression.png` — `--no-end-screen-cta`, same clip, same
  timestamp as the other frames. Captions render exactly as they did before
  this template existed; zero CTA overlay. Confirms the opt-out is a true
  no-op on the rest of the render.

## Durations checked (ffprobe)

- Standard, shorts: 8.042667s
- CALM, shorts: 8.042667s
- Standard, universal: 8.042667s
- `--no-end-screen-cta` (disabled): 8.042667s

All four identical — confirms the End Screen is a pure overlay on the
clip's own existing duration, never an append, in every preset/variant
combination, matching the QuoteCardIntro proof's own duration-check pattern
(that template DOES change duration; this one deliberately never does).

## Python bridge (`src/kinetic.py`) — real per-variant render, proven

`python -m src.kinetic <clip> <transcript.json> --start 0 --end 8 --out
py_bridge --highlight broken --banner "..." --safety ok` (end_screen_cta
defaults True) printed `finished 2 kinetic export(s) ... (2 unique
render(s))` and produced two genuinely DIFFERENT files
(`py_bridge_shorts.mp4` md5 `521cc91e...`, `py_bridge_universal.mp4` md5
`2866ddae...`) — confirms `finish()` now renders once per variant when the
End Screen CTA is on, replacing the old "no per-variant difference yet"
comment/behaviour from the KH Kinetic PR.

Calling `kinetic.finish(..., end_screen_cta=False)` directly printed
`finished 2 kinetic export(s) ... (1 unique render(s))` and produced two
BYTE-IDENTICAL files (`py_bridge_off_shorts.mp4` and
`py_bridge_off_universal.mp4`, both md5 `d2d28008...`) — confirms the
original v1 render-once-and-copy cost optimisation is preserved for the
disabled case, so opting out never pays for two identical renders.

Regenerate: see `render/README.md` "Try it", adding `--variant
shorts|universal` (and `--no-end-screen-cta` to opt a single render out) to
the `render-cli.mjs` invocation, or `python -m src.kinetic ... --no-end-
screen-cta` on the Python side.

## Coexistence with KH Quote Card intro (post-merge)

This template was built independently on `origin/main` before the KH Quote
Card intro (#24) and KH Audiogram v2 (#25) PRs merged — both touched the
same files (`Root.tsx`, `KHKinetic.tsx`, `brand.py`, `export_brand.py`,
`kinetic.py`, `render-cli.mjs`, `test_kinetic_bridge.py`). After merging
`origin/main` in, real renders confirm the two Wave 2 KHKinetic features
compose correctly with `--quote-card-intro --variant shorts` both set:

- `es_coexist_with_quote_card_intro.png` — the intro card at composition
  t=0.5s, unaffected by the End Screen change.
- `es_coexist_endscreen_shifted.png` — composition t=8.0s. The burned-in
  timecode reads `00:00:06.500` — exactly `8.0 - 1.5` (the intro's own
  duration), confirming the End Screen's Sequence (nested INSIDE
  `mainContent`, which the intro shifts as a whole block) correctly follows
  the shift with zero extra offset math, and renders with the exact same
  clean, non-overlapping layout as the no-intro proof frames above.
- Total duration with both features on: 9.557333s (video's own ~8.043s +
  the intro's 1.5s, matching `getIntroFrames()`'s math) — confirms the End
  Screen still contributes NOTHING to total length even with the intro
  active; only the intro extends the composition, exactly as designed.

Also re-ran `src.audiogram_v2` (the unrelated, independent KHAudiogramV2
composition) after the merge as a sanity check on the shared
`render-cli.mjs` dispatch refactor — real render, real frame pulled and
inspected, unaffected by this PR's changes.

Full suite after merge: `python -m pytest tests/` — 98 passed (matches
`origin/main`'s own post-#24/#25 baseline exactly, confirmed by running the
identical suite against a clean `git archive origin/main` checkout). `npx
tsc --noEmit` clean.
