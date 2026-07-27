# KH Clipper

KH Clipper is the Shorts render worker for Kintsugi Heroes. YouTube link in,
captioned vertical Shorts out. It runs as a Modal serverless GPU worker called
by kh-studio (the main app) via a web endpoint, or locally from the command line.

## Stack

- Python 3.12, Modal (serverless GPU deployment)
- ffmpeg (cut, reframe, caption burn)
- MediaPipe BlazeFace (face tracking for 9:16 speaker-follow crop)
- Grok STT (default transcription) + WhisperX (local fallback)
- Anthropic Claude (Shorts copy and metadata)
- Pillow + numpy (branded audiogram frames)

## Key commands

```bash
# Local run (full pipeline from a YouTube URL)
python clipper.py "https://youtube.com/watch?v=..."

# Deploy to Modal (requires modal token + secrets)
modal deploy worker/app.py

# Run tests
pytest tests/ -v
```

## Project structure

This is a pipeline worker, not a web app. kh-studio calls it via the Modal
endpoint. `clipper.py` is the CLI entry point. Each pipeline stage lives in
`src/` (fetch, transcribe, detect, cut, reframe, caption). The Modal wrapper
is `worker/app.py`. `brand_test.py` at the repo root is a visual QA smoke
test for branded output.

## Conventions

- Keep the worker focused on render. UI, scheduling, and distribution belong
  in kh-studio.
- Kintsugi Heroes only. Never mix with FFG, iamtonybailey, or TBS branding.
- The Kintsugi Pillar applies here. This worker renders real hero content,
  so every output must respect hero consent, dignity, and the KH voice.
- No em dashes in any output.

## Safety

- Never deploy without Tony's go.
- No force push, no history rewrite.
- All work on feature branches. Tony merges to main.
- Deploys happen via the GitHub Actions workflow on push to main.
