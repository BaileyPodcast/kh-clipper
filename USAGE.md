# KH Clipper — How to use it

Turns a long episode into finished, branded KH clips: Olive Pill karaoke captions,
gentle Subscribe / full-episode / linked-video CTAs, and your logo. Two exports per
clip (YouTube Shorts with arrows, and a universal version for Reels/TikTok).
Everything stays under 35 seconds and runs through the trauma-informed safety gate.

---

## One-time setup

```bash
cd kh-clipper
brew install ffmpeg            # Mac
pip install -r requirements.txt
export XAI_API_KEY="xai-..."   # your Grok key (for the smartest moment picks)
```

Fonts and logo are already in `assets/`. Nothing else to add.

---

## The normal run (one command)

```bash
python clipper.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

That does the whole thing: download audio, transcribe, find the Kintsugi moments,
cut them, reframe to 9:16, and burn captions + CTAs + logo.

Finished clips land in **`output/final/`**:

```
output/final/<id>-01_shorts.mp4      <- post to YouTube Shorts (arrows on)
output/final/<id>-01_universal.mp4   <- post to Reels / TikTok (no arrows)
```

---

## Handy options

```bash
# Skip the download — reuse a transcript you already made
python clipper.py --transcript output/<id>.transcript.json

# Cut from a local video file instead of downloading
python clipper.py "URL" --source /path/to/episode.mp4

# No Grok key handy? Use the offline heuristic detector
python clipper.py "URL" --no-llm

# Skip anything flagged for review, and cap at 30s
python clipper.py "URL" --safe-only --max-sec 30
```

---

## The producer gate (important)

Clips the AI flags as sensitive are tagged **review** in the run summary and in
`output/<id>.clips.json`. Watch those before posting. Anything the AI rates
`exclude` (sensational, undignified, exposes a non-consenting person) is dropped
automatically and never cut.

---

## Tuning (all in `src/brand.py`)

- **CTA wording** — `CTA["copy"]`
- **Arrow positions** (where they point on a Short) — `CTA["shorts_targets"]`.
  Watch one real Short on your phone and nudge these x,y numbers so the arrows
  land on the live Subscribe button / profile / related-video link.
- **Caption look** — `CAPTION` (font size, colours, position)
- **Logo size / position** — `LOGO`

Change a value once here and every future clip uses it.

---

## What's solid vs still basic

- Captions, CTAs, logo, safety gate, 35s cap: built and tested.
- Reframe is a simple centre-crop for now. For interviews where the guest sits
  centre-frame it's fine. Speaker-follow cropping (so it cuts to whoever's talking)
  is the next upgrade.
- The YouTube download path needs your normal yt-dlp setup (and cookies if the
  video needs them). Local `--source` always works.
```
