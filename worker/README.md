# KH Shorts — Modal worker

Runs the `kh-clipper` pipeline in the cloud so Kintsugi Studio can do
"paste a URL → Generate Shorts". The heavy work (yt-dlp + ffmpeg + MediaPipe + Grok)
runs here, never in a serverless function.

Full design: `../../2026-06-16-KH-Studio-Shorts-Engine-Integration-Build-Spec.md`.

## Flow
```
Studio  ──POST /generate (Bearer WORKER_TOKEN)──►  this worker
        ◄── Supabase shorts_jobs row (progress) ──  (worker patches the row)
        ◄── Supabase Storage 'shorts/<job_id>/' ──  (worker uploads outputs)
```

## 1. Deploy
```bash
pip install modal
modal token new

modal secret create kh-shorts \
  XAI_API_KEY=xai-... \
  SUPABASE_URL=https://<project>.supabase.co \
  SUPABASE_SERVICE_ROLE_KEY=<service-role-key> \
  WORKER_TOKEN=<a-long-random-shared-token>

modal deploy worker/app.py      # run from the kh-clipper repo root
```
Deploy prints the **web endpoint URL**. Give Studio two server-side env vars:
`KH_SHORTS_WORKER_URL` (that URL) and `KH_SHORTS_WORKER_TOKEN` (the same `WORKER_TOKEN`).

## 2. Supabase (once)
- Create a **private** Storage bucket named `shorts`.
- Apply the `shorts_jobs` table migration (see the integration spec §3) and enable
  **Realtime** on it.

## 3. Studio calls it
`POST /api/shorts/generate` (server route) inserts a `shorts_jobs` row, then:
```
POST {KH_SHORTS_WORKER_URL}
Authorization: Bearer {KH_SHORTS_WORKER_TOKEN}
{ "job_id": "<uuid>", "url": "<youtube>", "series": "golden-threads",
  "guest_name": "Jason Blyth", "count": 5, "audiogram": true,
  "reframe": "speaker" }
```
Returns `202 {accepted, job_id}` immediately; the job runs async. Studio watches the
`shorts_jobs` row via Realtime, then reads `outputs` (storage paths) and serves the
files via short-lived **signed URLs**.

`guest_name` (may be `null`) is threaded into the clip copy (titles/descriptions/pinned
comments); `reframe` is `speaker` (follow the speaker) or `center` (centre-crop).

## Per-clip ops — the "reframe" / "replace" buttons
The same endpoint + token handles per-clip re-renders, distinguished by `action`
(absent `action` = a normal full-generate job, unchanged):
```
POST {KH_SHORTS_WORKER_URL}   Authorization: Bearer {KH_SHORTS_WORKER_TOKEN}
{ "action": "reframe" | "replace",
  "job_id": "<existing shorts_jobs row>", "clip_id": "<clip in outputs.clips[]>",
  "reframe": "speaker" | "center",   // for action:"reframe"
  "url": "<original source>", "series": "<slug>", "guest_name": "<name or null>" }
```
- **reframe** re-cuts the SAME `start`/`end` with the new crop mode and swaps that clip's
  `files` (+ `framing`); copy/metadata stay put.
- **replace** picks the next-best UNUSED candidate moment (excluding every emitted clip's
  range) and swaps the whole entry, keeping the same `clip_id` so the card stays in place.

Progress + result are driven through a transient `clip_job` object **on the target clip**
inside `outputs` — the whole-job `status` stays `done` so the results view doesn't
collapse. `clip_job.status`: `queued` (kh-studio writes on trigger) → `running` →
`done` | `error`; cleared on success. Only that one clip's bytes change.

Both ops reuse the **persisted transcript + candidate pool** (see manifest below) — no
re-fetch, no re-transcribe.

## Outputs manifest (written to `shorts_jobs.outputs`)
```json
{
  "episode_id": "iTX6b2Z01II", "title": "...", "series": "golden-threads",
  "guest_name": "Jason Blyth",
  "clips": [
    { "clip_id": "...-01", "hook_line": "...", "why": "...",
      "safety": "ok", "framing": "ok",
      "metadata": { "title": "...", "description": "...", "hashtags": ["#shorts"],
                    "pinned_comment": "...", "banner_hook": "..." },
      "files": { "shorts": "shorts/<job>/...-01_shorts.mp4",
                 "universal": "shorts/<job>/...-01_universal.mp4",
                 "audiogram_square": "shorts/<job>/...-01_audiogram_square.mp4",
                 "audiogram_vertical": "shorts/<job>/...-01_audiogram_vertical.mp4" },
      "clip_job": { "action": "reframe", "status": "running", "progress": 40 } }
  ],
  "review": "shorts/<job>/REVIEW.md",
  "transcript": "shorts/<job>/transcript.json",
  "candidates": "shorts/<job>/candidates.json"
}
```
`clip_job` is present only while a per-clip op runs; `transcript`/`candidates` back the
per-clip ops (best-effort — the full job still succeeds if they fail to persist).

## Notes
- Image bundles `clipper.py`, `src/`, and `assets/` (fonts incl. Archivo/IBM Plex Mono,
  logos incl. the suite colourways in `assets/logo/suite/`, BlazeFace model, series
  artwork). `output/` and `venv/` are excluded.
- Audiograms (`audiogram_square`/`audiogram_vertical`) render the approved KH
  design-suite look per the series palette (`src/audiogram.py`); the on-screen text
  (caption = the clip's spoken hook line, footer title, `with {guest}`, series eyebrow)
  is generated per clip from that clip's own metadata, not a fixed placeholder.
- The trauma-informed gate travels with the code: each clip carries `safety`/`framing`
  flags; Studio must require human approval before any publish/schedule. Never auto-post.
- Cost: pay-per-second CPU, scales to zero. ~3–8 min CPU/episode ≈ a few cents.
- Modal API note: if your Modal version predates `fastapi_endpoint`, rename it to
  `web_endpoint` in `app.py`.
