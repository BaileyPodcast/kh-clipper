# KH-MGX-001 Wave 2 follow-up — visual proof (KH Audiogram v2)

Frames pulled from REAL renders through the unmodified `render-cli.mjs`'s
new `audiogram-v2` mode -> `@remotion/bundler` -> `@remotion/renderer`
pipeline (a real, locally-run headless Chromium, driven end to end via
`python -m src.audiogram_v2` — the real Python bridge, not a stub) on
synthetic test sources (this environment has no real KH footage or network
route to fetch one — the render pipeline itself, including the palette
resolution, envelope computation, caption grouping and CALM-preset logic,
is exactly production code, reused unmodified from `src/audiogram.py` where
noted). Every image below is an actual PNG frame extracted with `ffmpeg`
from an actual rendered MP4, inspected directly before being committed.

- `01-landscape-standard-entrance-start.png` — frame 0, standard preset.
  Every waveform bar starts collapsed (near-zero height) at the very first
  frame — the genuine spring-eased entrance in action, not present in the
  Pillow version (which has no entrance concept at all, every frame is
  independently drawn at full height).
- `02-landscape-standard-entrance-mid.png` — frame 5 (~166ms in), same
  render. Bars have sprung partway up on their staggered, left-to-right
  timing — confirms the entrance is a real animated ramp, not an instant
  on/off.
- `03-landscape-standard-reactive-midpoint.png` — frame 90 (3s in, halfway
  through a 6s clip with a real amplitude-modulated sine-wave audio track).
  The waveform shows BOTH the seeded envelope silhouette (taller in the
  middle, tapering at the edges — reused unmodified from
  `src/audiogram._seeded_bars()`) AND real audio reactivity (this clip's
  audio alternates loud/quiet every second; the bars visibly respond).
  Confirms `compute_envelope()`'s real-audio path (`_band_amps()`, also
  reused unmodified) is genuinely driving the render, not a stub.
- `04-landscape-standard-progress-glow.png` — frame 179 (the last frame of
  a 180-frame render). Progress bar at 100%, with a visible soft gold glow
  around the fill — the `progressGlow` preset flag doing real work, only
  active near completion (see `ProgressBar`'s `glowT` interpolation in
  `KHAudiogramV2.tsx`).
- `05-landscape-calm-no-pop-fades-only.png` — the SAME clip/caption/title
  as 01-04, `safety="review"` (CALM). Compare directly to
  `02-landscape-standard-entrance-mid.png` at the equivalent frame: the
  bars are ALREADY at their full computed height (no spring pop-in at all)
  — instead the WHOLE waveform + caption block is dimmed via a group
  opacity fade (visibly muted gold/grey vs. the crisp colours in the
  standard frames). This is the KH-MGX-001 locked decision #3 rule in
  action: CALM = fades only, no pop, no bounce.
- `06-landscape-calm-no-glow.png` — the CALM render's own last frame.
  Compare to `04-landscape-standard-progress-glow.png`: same 100% fill,
  but no glow — `progressGlow: false` under the CALM preset, confirmed.
- `07-vertical-timed-caption-chunk1.png` — vertical (1080x1920), Golden
  Threads palette, a real transcript with 13 words spanning ~5s, grouped by
  `src.audiogram.group_caption_lines()` (reused unmodified) into two
  chunks. Frame 20 (~0.67s) shows the FIRST chunk, "You are not broken.
  You are becoming."
- `08-vertical-timed-caption-chunk2.png` — frame 135 (~4.5s) of the SAME
  render: the caption has genuinely transitioned to the SECOND chunk,
  "The gold goes into the cracks." — confirms `timedLines` cross-fade
  transitions are real, time-driven, not a static caption.
- `09-square-no-audio-fallback-a.png` / `10-square-no-audio-fallback-b.png`
  — square (1080x1080), Grit Diaries palette (gold bg, olive waveform/ink —
  matches `src.audiogram.PALETTES["grit-diaries"]` exactly), rendered from
  a source clip with NO audio stream at all (confirmed via `ffprobe`:
  0 audio streams). `compute_envelope()` correctly falls back to `amps =
  None`, and the two frames (30 and 60) show visibly DIFFERENT bar shapes
  — the seeded decorative oscillation is genuinely animating, not frozen,
  exactly mirroring `src/audiogram.py`'s own fallback behaviour for a
  clip with unusable audio.
- `11-vertical-calm-longcaption-lineclamp.png` — Animals & Us palette
  (lime accent, matching `PALETTES["animals-and-us"]`), CALM preset, a
  genuinely long caption (over 3 lines' worth of text). Confirms the
  3-line CSS clamp (`WebkitLineClamp: 3`) truncates cleanly with an
  ellipsis instead of overflowing past the frame's accent border — and
  that CALM's group-fade behaviour holds on a non-landscape format too.

## What was actually run

```bash
cd render && npm ci && cd ..
python -m src.export_brand
export REMOTION_BROWSER_EXECUTABLE=/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell

python -m src.audiogram_v2 landscape_source.mp4 --series kintsugi-heroes \
  --caption "The gold goes into the cracks" --title "A story of repair" \
  --guest "Jane Doe" --ep "EP 14" --format landscape --safety ok \
  --out out_landscape_standard

python -m src.audiogram_v2 landscape_source.mp4 --series kintsugi-heroes \
  --caption "The gold goes into the cracks" --title "A story of repair" \
  --guest "Jane Doe" --ep "EP 14" --format landscape --safety review \
  --out out_landscape_calm

python -m src.audiogram_v2 vertical_source.mp4 vertical_transcript.json \
  --start 0 --end 5 --series golden-threads --title "Golden Threads" \
  --guest "Marcus Lee" --format vertical --safety ok \
  --out out_vertical_timed

python -m src.audiogram_v2 no_audio_source.mp4 --series grit-diaries \
  --caption "Grit is a daily practice" --title "Grit Diaries" \
  --format square --safety ok --out out_square_fallback
```

Regenerate: see `render/README.md`'s new "KH Audiogram v2" section.
