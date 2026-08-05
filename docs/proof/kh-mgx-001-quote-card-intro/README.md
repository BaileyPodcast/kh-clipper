# KH-MGX-001 Wave 2 — visual proof (KH Quote Card intro)

Frames pulled from REAL renders through the unmodified `render-cli.mjs` ->
`@remotion/bundler` -> `@remotion/renderer` pipeline (a real, locally-run
headless Chromium, not mocked), on the same synthetic test source used for
the Wave 2 KH Kinetic proof (this environment has no real KH footage or
network route to fetch one — the render pipeline itself, including the
intro/transition/CALM logic, is exactly production code).

The Quote Card intro is opt-in (`--quote-card-intro`, off by default) and is
a no-op without a `--banner`. When on, the composition's total length grows
by the intro's own duration (`brand.animation.quoteCardIntro.durationSec`,
1.5s by default) via `getIntroFrames()` — `calculateMetadata` in `Root.tsx`
is the one place this math lives, so it can never drift from what the
component itself lays out.

- `qc_intro_mid.png` — standard preset, mid-fade into the intro card. Full-
  bleed dark-olive background, the hook line ("The moment everything
  changed") in cream headline type, a gold accent bar. Spring-based entrance
  (drift + fade) under the standard preset.
- `qc_after_transition.png` — the first frame of the underlying footage,
  right after the intro hands off. The test clip's own burned-in timecode
  overlay confirms the footage restarts from its own t=0 exactly when the
  intro ends (no frames skipped, no dead air).
- `qc_captions_after_intro.png` — further into the footage: captions AND the
  on-video banner both render correctly on top of the footage after the
  intro, exactly matching the pre-intro (no-`--quote-card-intro`) behaviour —
  the intro only prepends, it never changes what plays after it.
- `qc_calm_intro.png` — CALM preset (`--safety review`), same banner text.
  Fade-only, no drift/scale entrance motion — matches KH-TIC-001's
  trauma-informed rule (Wave 1's CALM guard, and the Wave 2 Quote Card's own
  locked decision #3: any clip with `safety != "ok"` gets fades only).

## Durations checked (ffprobe)

- Standard, with intro: 7.552s = 6.0s video + 1.5s intro (rounded to whole
  frames at the clip's fps).
- CALM, with intro: 7.552s — identical total, confirming the intro's own
  duration doesn't change between presets, only its entrance motion.
- Regression check, intro OFF (no `--quote-card-intro`): 6.058667s — matches
  pre-change render durations from the Wave 2 proof set exactly. Zero
  regression to the default (intro-off) path.

Regenerate: see `render/README.md` "Try it", adding `--quote-card-intro
--banner "..."` to the `kinetic.py` / `render-cli.mjs` invocation.
