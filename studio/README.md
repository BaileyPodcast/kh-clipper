# KH Studio — Shorts Engine (drop-in package)

Everything the Studio side needs, ready to copy into the Studio repo. Full design:
`../../2026-06-16-KH-Studio-Shorts-Engine-Integration-Build-Spec.md`.

## Files here
| File | Goes to (Studio repo) | What it is |
|---|---|---|
| `db_shorts_jobs.sql` | `db/NNN_shorts_jobs.sql` | Table + section RLS + `set_updated_at` trigger + private `shorts` bucket + Realtime. Verified against the live project's conventions. |
| `app/api/shorts/generate/route.ts` | `app/api/shorts/generate/route.ts` | Inserts a job row, triggers the Modal worker (token server-side). |
| `app/studio/shorts/page.tsx` | `app/studio/shorts/page.tsx` | The page: URL + series + Generate → live progress → results gallery → approve. |

The worker itself is in `../worker/` (deploy with Modal).

## Assembly order
1. **DB** — apply `db_shorts_jobs.sql` to the `Kintsugi Heroes Production Pipeline`
   project (as a committed `db/NNN_*.sql`). Enables RLS + Realtime + the `shorts` bucket.
2. **Worker** — from `kh-clipper/`: `modal deploy worker/app.py` (see `../worker/README.md`).
   Note the endpoint URL.
3. **Env (Studio, server-side)**:
   ```
   KH_SHORTS_WORKER_URL=<modal endpoint>
   KH_SHORTS_WORKER_TOKEN=<same WORKER_TOKEN as the Modal secret>
   ```
4. **Code** — copy the two `app/...` files in. Swap the two `TODO` Supabase imports
   for the project's existing helpers (`@/lib/supabase/server` and `.../client`, or
   whatever the repo uses — copy from an existing `/studio` page + `/api` route).
5. **Access** — make sure `/studio/shorts` sits behind the same `studio`-section guard
   as the other studio pages (the RLS + `can_*_section('studio')` already enforce data
   access; this is just the route guard).

## Adapt notes
- The page reads private files via `supabase.storage.createSignedUrl` using the
  member's session — allowed by the `shorts_obj_read` policy (`can_read_section('studio')`).
- `clip_count` (not `count`) is the column name.
- The **approve** toggle is local state in this starter. Phase 2: persist approvals and
  wire approved clips into the Scheduling Calendar + the existing governance/consent gate
  (`hero_consent`, `governance_reviews`) before anything schedules/publishes. Never auto-post.
