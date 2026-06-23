# KH Clipper

Standalone tool: YouTube link in → captioned vertical Shorts out.
Kept separate from `podcast-pipeline` on purpose.

## How it works
A staged pipeline. Each stage is one file in `src/`:

0. **fetch** — yt-dlp pulls audio-only from the URL            ✅ built
1. **transcribe** — Grok STT (default) / WhisperX (fallback)   ✅ built
2. **detect** — find clip-worthy moments
3. **cut** — yt-dlp section pull + ffmpeg slice
4. **reframe** — 16:9 → 9:16
5. **caption** — burn subtitles back on

Full video never sits on disk. Transcribe tiny audio first, then
pull only the 1080p video sections we actually clip.

## Setup
```bash
brew install ffmpeg            # one-time, system-level
pip install -r requirements.txt
export XAI_API_KEY="your-key"  # for Grok (default provider)
```

## Run
```bash
python clipper.py "https://youtube.com/watch?v=..."        # Grok (default)
python clipper.py "https://youtube.com/watch?v=..." --provider whisperx
```

If Grok fails (no key / no internet), it auto-falls back to local WhisperX.

## Transcription providers
- **grok**     — xAI Grok STT API. ~$0.10/hr. Fast, off your Mac. Needs `XAI_API_KEY`.
- **whisperx** — local, free, private. Use for sensitive interviews. Heavier on the Mac.

## Status
Stages 0–1 built. Stages built one at a time from here.
