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
  "count": 5, "audiogram": true }
```
Returns `202 {accepted, job_id}` immediately; the job runs async. Studio watches the
`shorts_jobs` row via Realtime, then reads `outputs` (storage paths) and serves the
files via short-lived **signed URLs**.

## Outputs manifest (written to `shorts_jobs.outputs`)
```json
{
  "episode_id": "iTX6b2Z01II", "title": "...", "series": "golden-threads",
  "clips": [
    { "clip_id": "...-01", "hook_line": "...", "why": "...",
      "safety": "ok", "framing": "ok",
      "metadata": { "title": "...", "description": "...", "hashtags": ["#shorts"],
                    "pinned_comment": "...", "banner_hook": "..." },
      "files": { "shorts": "shorts/<job>/...-01_shorts.mp4",
                 "universal": "shorts/<job>/...-01_universal.mp4",
                 "audiogram_square": "shorts/<job>/...-01_audiogram_square.mp4",
                 "audiogram_vertical": "shorts/<job>/...-01_audiogram_vertical.mp4" } }
  ],
  "review": "shorts/<job>/REVIEW.md"
}
```

## Notes
- Image bundles `clipper.py`, `src/`, and `assets/` (fonts, logo, BlazeFace model,
  series artwork). `output/` and `venv/` are excluded.
- The trauma-informed gate travels with the code: each clip carries `safety`/`framing`
  flags; Studio must require human approval before any publish/schedule. Never auto-post.
- Cost: pay-per-second CPU, scales to zero. ~3–8 min CPU/episode ≈ a few cents.
- Modal API note: if your Modal version predates `fastapi_endpoint`, rename it to
  `web_endpoint` in `app.py`.
