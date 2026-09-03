"""
KH Shorts — Modal worker.

Runs the kh-clipper pipeline in the cloud so Kintsugi Studio can offer "paste a URL
-> Generate Shorts" without a terminal. Studio inserts a `shorts_jobs` row and calls
the web endpoint below; this worker runs clipper.run(), streams progress into the job
row, uploads the finished files to Supabase Storage, and marks the job done.

The heavy work (yt-dlp + ffmpeg + MediaPipe + Grok) runs HERE, never in a Vercel/
serverless function. See 2026-06-16-KH-Studio-Shorts-Engine-Integration-Build-Spec.md.

Deploy:
    pip install modal
    modal token new
    modal secret create kh-shorts \
        XAI_API_KEY=xai-... \
        SUPABASE_URL=https://<project>.supabase.co \
        SUPABASE_SERVICE_ROLE_KEY=<service-role-key> \
        WORKER_TOKEN=<a-long-random-shared-token> \
        GOOGLE_OAUTH_CLIENT_ID=<client-id> \
        GOOGLE_OAUTH_CLIENT_SECRET=<client-secret>
    modal secret create KH-clipper-worker ANTHROPIC_API_KEY=sk-ant-...   # Shorts copy (Brief 3); same key as the app
    modal deploy worker/app.py
    # -> prints the web endpoint URL. Put it + WORKER_TOKEN in Studio's server env.

    GOOGLE_OAUTH_CLIENT_ID / _SECRET are only needed for the Schedule-for-YouTube
    upload job (action="upload_youtube"): the worker reads the channel refresh token
    from oauth_tokens (service role) and refreshes its own YouTube access token, so
    no token is sent in the request. Shorts jobs don't use them.

Storage: a PRIVATE bucket named `shorts`. Studio reads via short-lived signed URLs.
The action="video" landscape audiogram job writes to a second private bucket,
`studio-video`, and streams progress into `studio_video_jobs` (KH-VRL-001).
"""
import os
import re

import fastapi          # provided by Modal's client for web endpoints
import modal

APP_NAME = "kh-shorts-worker"
BUCKET = "shorts"
VIDEO_BUCKET = "studio-video"       # landscape audiogram outputs (action="video")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Image: Python 3.12 (broad compatibility) + ffmpeg + the pipeline deps + the repo.
# Local code/assets are copied in as the final layers. `output/` and `venv/` are
# excluded — they are not needed in the image.
#
# KH-MGX-001 Wave 2: Node.js is added to THIS SAME image (single Modal function,
# no cross-function orchestration) so the worker can shell out to the Remotion
# render layer (render/render-cli.mjs, invoked via src/kinetic.py) for
# caption_style="kinetic" — "classic" (default) never touches Node at all.
# NodeSource's own setup script pins an exact major (22, matching what this repo
# was built/tested against), rather than trusting whatever Debian's apt currently
# ships. `npm ci` runs at IMAGE BUILD time (render/ copied in with copy=True so
# it's baked into this layer, not a runtime mount) so a cold worker start never
# waits on the npm registry. No browser is bundled here — Remotion downloads and
# caches its own Chrome Headless Shell on first render (its documented default
# for server/CI environments); set REMOTION_BROWSER_EXECUTABLE in the image (or
# a Modal secret) later if that first-render cold-start cost needs trimming.
image = (
    modal.Image.debian_slim(python_version="3.12")
    # ffmpeg for cut/reframe/caption; the GL libs are MediaPipe's runtime deps — without
    # them BlazeFace fails to init (libGLESv2.so.2 missing) and every clip silently
    # centre-crops instead of following the speaker (see src/reframe.py fail-soft path).
    .apt_install("ffmpeg", "libgl1", "libglib2.0-0", "libegl1", "libgles2",
                 "curl", "ca-certificates", "gnupg")
    # pillow + numpy power the branded audiogram renderer (src/audiogram.py): it draws
    # each frame of the KH design-suite audiogram and ffmpeg muxes the clip audio under it.
    .pip_install("yt-dlp", "requests", "mediapipe", "ffmpeg-python", "fastapi[standard]",
                 "gdown", "pillow", "numpy", "anthropic")
    .run_commands(
        "curl -fsSL https://deb.nodesource.com/setup_22.x | bash -",
        "apt-get install -y nodejs",
    )
    # copy=True on every add_local_* below: Modal only allows a build step (the
    # npm ci at the end) to run after add_local_* calls when ALL of them copy
    # into the image layer rather than mount at container start — confirmed by
    # a real failed deploy (KH-MGX-001 Wave 2's first attempt): "An image tried
    # to run a build step after using image.add_local_* ... set copy=True".
    # node_modules/brand.json are gitignored (never present in a clean checkout).
    .add_local_file(os.path.join(REPO_ROOT, "clipper.py"), "/root/clipper.py", copy=True)
    .add_local_dir(os.path.join(REPO_ROOT, "src"), "/root/src", copy=True)
    .add_local_dir(os.path.join(REPO_ROOT, "assets"), "/root/assets", copy=True)
    .add_local_dir(os.path.join(REPO_ROOT, "render"), "/root/render", copy=True)
    .run_commands("cd /root/render && npm ci --omit=dev")
)

# Perf tuning (kh-clipper stack audit, 2026-08-19): every job function here is
# CPU-bound (ffmpeg cut/reframe/caption/audiogram, MediaPipe face detection) and
# none of them requested a CPU size before, so Modal fell back to its small
# default allotment and every per-clip ffmpeg subprocess competed for a sliver
# of a core. `cpu=` below is a REQUEST (Modal bills + throttles at that amount,
# it does not change what os.cpu_count() reports inside the container), sized
# to give clipper.py's per-clip ThreadPoolExecutor (see clipper.py
# CLIP_RENDER_WORKERS) real cores to spread across rather than one CPU shared
# N ways. `min_containers=1` on process_clip_job specifically keeps ONE
# container warm: it's the interactive reframe/replace path a producer clicks
# and waits on live, so a cold start (fresh mediapipe/node import) there is the
# most costly one to eat on every click. The batch `process_job` path is
# usually kicked off and left, so a cold start costs less there and doesn't
# get a warm pool (avoids paying for idle containers with no clear win).
JOB_CPU = 4.0

app = modal.App(APP_NAME)
SECRET = modal.Secret.from_name("kh-shorts")
# Cookies live in their own secret so refreshing them is one trivial command and
# never touches the API keys. (Create the `yt-cookies` secret before deploying.)
COOKIE_SECRET = modal.Secret.from_name("yt-cookies")
# xAI key in its own secret too — single-value, easy to set/rotate. Listed LAST so its
# XAI_API_KEY overrides any stale value in kh-shorts.
XAI_SECRET = modal.Secret.from_name("xai")
# Anthropic key for the Shorts copy pass (Brief 3). Same key as the app (one bill).
# The Modal secret is named `KH-clipper-worker` and must hold ANTHROPIC_API_KEY=sk-ant-...
# (src/metadata.py reads os.environ["ANTHROPIC_API_KEY"]). Create/edit it in the Modal
# dashboard, or:  modal secret create KH-clipper-worker ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_SECRET = modal.Secret.from_name("KH-clipper-worker")

CONTENT_TYPES = {".mp4": "video/mp4", ".md": "text/markdown", ".json": "application/json"}


# ----------------------------------------------------------------------
# Supabase helpers (REST + Storage) using the service-role key.
# ----------------------------------------------------------------------
def _sb_headers(extra=None):
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    if extra:
        h.update(extra)
    # HTTP headers must be latin-1 encodable; drop any stray non-ascii so a bad paste
    # in a secret can never crash the request (it would just fail auth cleanly).
    return {k: str(v).encode("ascii", "ignore").decode() for k, v in h.items()}


def _sb_url():
    return os.environ["SUPABASE_URL"].strip()


def patch_job(job_id, fields):
    """PATCH a shorts_jobs row (service role bypasses RLS). Never raises.
    `updated_at` is handled by the shorts_jobs_set_updated_at DB trigger."""
    import json as _json
    import requests
    try:
        body = _json.dumps(fields, ensure_ascii=True)          # ascii-safe body, always
        r = requests.patch(
            f"{_sb_url()}/rest/v1/shorts_jobs",
            headers=_sb_headers({"Content-Type": "application/json",
                                 "Prefer": "return=minimal"}),
            params={"id": f"eq.{job_id}"},
            data=body, timeout=30,
        )
        if r.status_code >= 300:
            print(f"patch_job {r.status_code}: {r.text[:200]}")
    except Exception as e:                       # progress is best-effort
        print(f"patch_job failed: {e}")


def patch_video_job(job_id, fields):
    """PATCH a studio_video_jobs row (service role bypasses RLS). Never raises.
    Columns: status, stage, progress, message, error, output_url."""
    import json as _json
    import requests
    try:
        body = _json.dumps(fields, ensure_ascii=True)
        r = requests.patch(
            f"{_sb_url()}/rest/v1/studio_video_jobs",
            headers=_sb_headers({"Content-Type": "application/json",
                                 "Prefer": "return=minimal"}),
            params={"id": f"eq.{job_id}"},
            data=body, timeout=30,
        )
        if r.status_code >= 300:
            print(f"patch_video_job {r.status_code}: {r.text[:200]}")
    except Exception as e:                       # progress is best-effort
        print(f"patch_video_job failed: {e}")


def patch_audiogram_job(job_id, fields):
    """PATCH a studio_audiogram_jobs row (service role bypasses RLS). Never raises.
    Columns: status, stage, progress, message, error, moment_start_sec,
    moment_end_sec, output_square_url, output_vertical_url."""
    import json as _json
    import requests
    try:
        body = _json.dumps(fields, ensure_ascii=True)
        r = requests.patch(
            f"{_sb_url()}/rest/v1/studio_audiogram_jobs",
            headers=_sb_headers({"Content-Type": "application/json",
                                 "Prefer": "return=minimal"}),
            params={"id": f"eq.{job_id}"},
            data=body, timeout=30,
        )
        if r.status_code >= 300:
            print(f"patch_audiogram_job {r.status_code}: {r.text[:200]}")
    except Exception as e:                       # progress is best-effort
        print(f"patch_audiogram_job failed: {e}")


def get_job(job_id):
    """Fetch one shorts_jobs row (service role). Returns the row dict or None."""
    import requests
    r = requests.get(
        f"{_sb_url()}/rest/v1/shorts_jobs",
        headers=_sb_headers(), params={"id": f"eq.{job_id}", "select": "*"}, timeout=30,
    )
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None


def patch_upload(upload_id, fields):
    """PATCH a youtube_uploads row (service role bypasses RLS). Never raises.
    `updated_at` is handled by the youtube_uploads_set_updated_at DB trigger."""
    import json as _json
    import requests
    try:
        body = _json.dumps(fields, ensure_ascii=True)
        r = requests.patch(
            f"{_sb_url()}/rest/v1/youtube_uploads",
            headers=_sb_headers({"Content-Type": "application/json",
                                 "Prefer": "return=minimal"}),
            params={"id": f"eq.{upload_id}"},
            data=body, timeout=30,
        )
        if r.status_code >= 300:
            print(f"patch_upload {r.status_code}: {r.text[:200]}")
    except Exception as e:                       # progress is best-effort
        print(f"patch_upload failed: {e}")


def _youtube_token():
    """Mint a YouTube access token from the stored refresh token: a service-role read
    of oauth_tokens (provider=youtube, account_label=kh-primary) + a Google refresh.
    Needs GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET in the kh-shorts secret.
    Raises with a clear message when the channel isn't connected."""
    import requests
    r = requests.get(
        f"{_sb_url()}/rest/v1/oauth_tokens",
        headers=_sb_headers(),
        params={"provider": "eq.youtube", "account_label": "eq.kh-primary",
                "select": "refresh_token"},
        timeout=30,
    )
    r.raise_for_status()
    rows = r.json()
    refresh = rows[0].get("refresh_token") if rows else None
    if not refresh:
        raise RuntimeError("YouTube isn't connected (no refresh token in oauth_tokens).")
    tr = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"].strip(),
            "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"].strip(),
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    tr.raise_for_status()
    tok = tr.json().get("access_token")
    if not tok:
        raise RuntimeError("Google token refresh returned no access_token.")
    return tok


def download_storage(storage_path, local_path):
    """Download an object from the private bucket. `storage_path` is what upload_file
    returned (e.g. 'shorts/<job_id>/transcript.json' — the bucket prefix is included)."""
    import requests
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    r = requests.get(f"{_sb_url()}/storage/v1/object/{storage_path}",
                     headers=_sb_headers(), timeout=300)
    r.raise_for_status()
    with open(local_path, "wb") as f:
        f.write(r.content)
    return local_path


def patch_clip(job_id, clip_id, *, files=None, framing=None, replace_entry=None,
               clip_job="__keep__"):
    """Patch a SINGLE clip inside outputs.clips[] (read-modify-write), never touching the
    row `status` or any other clip — so the kh-studio results view stays intact and the
    whole-job status stays 'done'. Drives per-clip progress via the clip's `clip_job`.

    - clip_job=dict  -> set outputs.clips[i].clip_job (progress/result of this op)
    - clip_job=None  -> drop clip_job (op finished cleanly)
    - clip_job='__keep__' -> leave clip_job as-is (e.g. when only swapping files)
    - replace_entry  -> swap the whole clip entry (keeps the same clip_id)
    - files/framing  -> patch just those fields on the existing entry
    """
    job = get_job(job_id)
    if not job:
        print(f"patch_clip: job {job_id} not found")
        return
    outputs = job.get("outputs") or {}
    clips = outputs.get("clips") or []
    for i, c in enumerate(clips):
        if c.get("clip_id") != clip_id:
            continue
        if replace_entry is not None:
            replace_entry["clip_id"] = clip_id    # the card stays in place
            clips[i] = replace_entry
        else:
            if files is not None:
                c["files"] = files
            if framing is not None:
                c["framing"] = framing
            if clip_job == "__keep__":
                pass
            elif clip_job is None:
                c.pop("clip_job", None)
            else:
                c["clip_job"] = clip_job
        break
    outputs["clips"] = clips
    patch_job(job_id, {"outputs": outputs})       # only `outputs` — never `status`


def upload_clip_files(job_id, clip_id, files, tag):
    """Upload one clip's freshly-rendered files under unique (cache-busting) names and
    return a {kind: storage_path} manifest. `tag` keeps re-renders from colliding."""
    out = {}
    for kind, path in (files or {}).items():
        if path and os.path.exists(path):
            ext = os.path.splitext(path)[1]
            fname = f"{KIND_LABEL.get(kind, kind)} {clip_id} {tag}{ext}"
            out[kind] = upload_file(path, f"{job_id}/{fname}")
    return out


def upload_file(local_path, remote_path, bucket=BUCKET):
    """Upload one file to a private Storage bucket (upsert). Returns the storage path.
    Defaults to the `shorts` bucket so every existing call is untouched."""
    import requests
    ext = os.path.splitext(local_path)[1].lower()
    with open(local_path, "rb") as f:
        r = requests.post(
            f"{_sb_url()}/storage/v1/object/{bucket}/{remote_path}",
            headers=_sb_headers({"Content-Type": CONTENT_TYPES.get(ext, "application/octet-stream"),
                                 "x-upsert": "true"}),
            data=f.read(), timeout=300,
        )
    r.raise_for_status()
    return f"{bucket}/{remote_path}"


# Human-readable file labels so downloads are obvious (not cryptic clip ids).
KIND_LABEL = {
    "shorts": "Shorts Clip",
    "universal": "Reel-TikTok Clip",
    "audiogram_square": "Audiogram Square Clip",
    "audiogram_vertical": "Audiogram Vertical Clip",
    "audiogram_landscape": "Audiogram Landscape",
}


# ----------------------------------------------------------------------
# action="video" (kh-studio KH-VRL-001): validation + the landscape audiogram job.
# ----------------------------------------------------------------------
VIDEO_FORMATS = {"audiogram_landscape"}
VIDEO_WINDOW_MIN_SEC = 10
VIDEO_WINDOW_MAX_SEC = 180


def validate_video_payload(payload):
    """Validate an action="video" payload. Returns None when valid, else a short
    human-readable reason (the endpoint turns it into a 400). Pure stdlib so it
    is unit-testable without Modal or fastapi."""
    if not isinstance(payload, dict):
        return "payload must be a JSON object"
    for field in ("job_id", "url", "series"):
        v = payload.get(field)
        if not isinstance(v, str) or not v.strip():
            return f"missing {field}"
    fmt = payload.get("format")
    if fmt not in VIDEO_FORMATS:
        return f"unknown format {fmt!r}"
    start, end = payload.get("start_sec"), payload.get("end_sec")
    for name, v in (("start_sec", start), ("end_sec", end)):
        if isinstance(v, bool) or not isinstance(v, int):
            return f"{name} must be an integer"
    if start < 0:
        return "start_sec must be >= 0"
    if start >= end:
        return "start_sec must be before end_sec"
    window = end - start
    if window < VIDEO_WINDOW_MIN_SEC or window > VIDEO_WINDOW_MAX_SEC:
        return (f"window must be {VIDEO_WINDOW_MIN_SEC}..{VIDEO_WINDOW_MAX_SEC} "
                f"seconds (got {window})")
    return None


def upload_outputs(job_id, result):
    """Upload every finished file + REVIEW.md, returning a manifest with storage paths
    (not local paths) plus all the per-clip metadata/flags Studio needs to render.
    Files are named e.g. 'Shorts Clip 1.mp4' so they're obvious to download."""
    clips = []
    for i, clip in enumerate(result["clips"], 1):
        files = {}
        for kind, path in clip.get("files", {}).items():
            if path and os.path.exists(path):
                ext = os.path.splitext(path)[1]
                fname = f"{KIND_LABEL.get(kind, kind)} {i}{ext}"
                files[kind] = upload_file(path, f"{job_id}/{fname}")
        clips.append({**{k: v for k, v in clip.items() if k != "files"}, "files": files})
    review = None
    if result.get("review_md_path") and os.path.exists(result["review_md_path"]):
        review = upload_file(result["review_md_path"], f"{job_id}/REVIEW.md")
    return {"episode_id": result.get("episode_id"), "title": result.get("title"),
            "series": result.get("series"), "guest_name": result.get("guest_name"),
            "clips": clips, "review": review,
            # Brief 2: which transcription path ran (reuse_assemblyai | grok_stt | whisperx).
            "transcript_source": result.get("transcript_source"),
            # KH-CTP-001: the job's selection lens, echoed at job level too
            # (every clips[] entry also carries its own clip_type).
            "clip_type": result.get("clip_type", "best"),
            # KH-MGX-001 defect fix: caption_style was never persisted into
            # outputs, so process_clip_job's outputs.get("caption_style")
            # always fell back to "classic" and a kinetic job's reframe/replace
            # silently re-rendered classic. Persist it so per-clip ops keep the
            # style the job was rendered with.
            "caption_style": result.get("caption_style", "classic"),
            # KH-CTP-001 Phase 2: a spread run's requested type order + the honest
            # per-type outcome ([{"type","found","reason"}]) so Studio can show
            # "N of M requested" instead of a silent short count. None outside a
            # spread run (single-type/exact-cut jobs are unaffected).
            "spread_types": result.get("spread_types"),
            "spread_report": result.get("spread_report")}


# ----------------------------------------------------------------------
# The job: run the pipeline, stream progress, upload outputs.
# ----------------------------------------------------------------------
def _write_cookies():
    """Write the YT_COOKIES secret to a file yt-dlp can read (shared by full + per-clip
    jobs). Datacenter IPs hit YouTube's bot wall without cookies."""
    cookies = os.environ.get("YT_COOKIES")
    print(f"[cookies] YT_COOKIES bytes={len(cookies) if cookies else 0}")
    if cookies and cookies.strip():
        with open("/tmp/yt_cookies.txt", "w") as f:
            f.write(cookies)
        os.environ["YT_COOKIES_FILE"] = "/tmp/yt_cookies.txt"
        n = sum(1 for ln in cookies.splitlines() if ln.strip() and not ln.startswith("#"))
        print(f"[cookies] wrote /tmp/yt_cookies.txt with {n} cookie lines")
    else:
        print("[cookies] no usable YT_COOKIES — yt-dlp will hit the bot wall")


def _youtube_id(url: str) -> str:
    """The 11-char YouTube id from a watch/short/embed link. The clipper rebuilds
    the remote cut URL from the transcript's `id`, so a supplied transcript must
    carry the real video id on the YouTube path."""
    import re
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else "episode"


def _media_duration_local(path: str):
    """Seconds of a local media file via ffprobe, or None if it can't be read."""
    import subprocess
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=120)
        return float(out.stdout.strip())
    except Exception:
        return None


def _prepare_supplied_transcript(transcript, ep_id: str, media_path: str = None):
    """Plan B: turn kh-studio's supplied transcript (a dict of title/text/words/
    duration_sec) into a transcript file the clipper uses INSTEAD of its own STT.
    Returns the written path, or None to fall back to transcribing the source.

    `ep_id` becomes the transcript `id` (the clipper rebuilds the YouTube cut URL
    from it, so it MUST be the real video id on the YouTube path). When a local
    media file is on hand (the Drive path), we sanity-check that the transcript's
    timeline fits the media before trusting it — a wildly different length means a
    different edit, so we re-transcribe rather than mis-cut. On the YouTube path we
    trust provenance (Studio only attaches that same episode's own transcript) and
    rely on the mandatory human clip review as the backstop."""
    try:
        if not transcript:
            return None
        words = transcript.get("words") or []
        if not words:
            return None
        last_end = max((float(w.get("end", 0) or 0) for w in words), default=0.0)
        if last_end <= 0:
            return None
        if media_path:
            dur = _media_duration_local(media_path)
            # Transcript must fit inside the media (small slack), and the media
            # must not be a wildly different length. Bad fit -> re-transcribe.
            if dur and not (last_end <= dur * 1.05 and dur <= last_end * 2.0):
                print(f"[plan-b] transcript/media duration mismatch "
                      f"(transcript {last_end:.0f}s vs media {dur:.0f}s) — re-transcribing")
                return None
        import json as _json
        t = dict(transcript)
        t["id"] = ep_id
        if not t.get("text"):
            t["text"] = " ".join(str(w.get("text", "")) for w in words)
        os.makedirs("/tmp/job", exist_ok=True)
        path = f"/tmp/job/{ep_id}.transcript.json"
        with open(path, "w") as f:
            _json.dump(t, f)
        print(f"[plan-b] using kh-studio transcript ({len(words)} words) — skipping STT")
        return path
    except Exception as e:
        print(f"[plan-b] supplied transcript unusable ({str(e)[:120]}) — re-transcribing")
        return None


@app.function(image=image, timeout=1800, cpu=JOB_CPU,
             secrets=[SECRET, COOKIE_SECRET, XAI_SECRET, ANTHROPIC_SECRET])
def process_job(job_id: str, url: str, series: str = None,
                count: int = 5, audiogram: bool = True, reframe: str = "speaker",
                guest_name: str = None, transcript: dict = None, moments: list = None,
                caption_style: str = "classic", clip_type: str = "best",
                reviewer_anchors: list = None, hook_phrases: list = None,
                clip_types: list = None):
    import sys
    sys.path.insert(0, "/root")
    os.chdir("/root")
    # YouTube blocks datacenter IPs; if a YT_COOKIES secret is present, write it to a
    # file and point yt-dlp at it (src/ytdlp.py reads YT_COOKIES_FILE).
    _write_cookies()
    import clipper

    def progress(stage, pct, msg=""):
        patch_job(job_id, {"status": "running", "stage": stage,
                           "progress": int(pct), "message": msg})

    try:
        patch_job(job_id, {"status": "running", "stage": "queued", "progress": 0})
        # Google Drive master -> download it and process locally (no YouTube, no cookies).
        import re
        drive = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?id=|.*[?&]id=)([A-Za-z0-9_-]{20,})", url)
        if drive:
            file_id = drive.group(1)
            progress("download", 3, "downloading master from Google Drive")
            import gdown
            os.makedirs("/tmp/job", exist_ok=True)
            src = f"/tmp/job/{file_id}.mp4"
            gdown.download(id=file_id, output=src, quiet=True)
            if not os.path.exists(src) or os.path.getsize(src) < 10000:
                raise RuntimeError("Drive download failed — is the file shared 'anyone with the link'?")
            tpath = _prepare_supplied_transcript(transcript, file_id, media_path=src)
            result = clipper.run(
                source_file=src, episode_id=file_id, series=series, count=count,
                make_audiogram=audiogram, progress_cb=progress, output_root="/tmp/job",
                reframe_mode=reframe, guest_name=guest_name, transcript=tpath,
                moments=moments, caption_style=caption_style,
                clip_type=clip_type, reviewer_anchors=reviewer_anchors,
                hook_phrases=hook_phrases, clip_types=clip_types,
                usage_ctx={"job_id": job_id, "source": "worker", "episode_ref": file_id},
            )
        else:
            tpath = _prepare_supplied_transcript(transcript, _youtube_id(url))
            result = clipper.run(
                url=url, series=series, count=count, make_audiogram=audiogram,
                progress_cb=progress, output_root="/tmp/job", reframe_mode=reframe,
                guest_name=guest_name, transcript=tpath, moments=moments,
                caption_style=caption_style,
                clip_type=clip_type, reviewer_anchors=reviewer_anchors,
                hook_phrases=hook_phrases, clip_types=clip_types,
                usage_ctx={"job_id": job_id, "source": "worker", "episode_ref": _youtube_id(url)},
            )
        patch_job(job_id, {"stage": "uploading", "progress": 96, "message": "uploading outputs"})
        outputs = upload_outputs(job_id, result)
        # Persist the transcript + candidate pool so the per-clip reframe/replace buttons
        # can re-cut a single moment WITHOUT re-fetching/re-transcribing. Best-effort:
        # the full job still succeeds even if these uploads fail (the buttons just won't
        # have a fast path). Storage paths are recorded back into `outputs`.
        try:
            import json as _json
            tpath = result.get("transcript_path")
            if tpath and os.path.exists(tpath):
                outputs["transcript"] = upload_file(tpath, f"{job_id}/transcript.json")
            cands = result.get("candidate_pool") or []
            cpath = "/tmp/job/candidates.json"
            with open(cpath, "w") as f:
                _json.dump(cands, f)
            outputs["candidates"] = upload_file(cpath, f"{job_id}/candidates.json")
        except Exception as e:
            print(f"persist transcript/candidates failed (per-clip ops degrade): {e}")
        # KH-CTP-001 Phase 2: an honest "N of M requested" message on a spread
        # run whose transcript genuinely didn't have every requested type in
        # it — never a silent short count. Single-type/exact-cut jobs are
        # unaffected (spread_types is None, message unchanged).
        n_clips = len(outputs["clips"])
        spread_types = outputs.get("spread_types")
        if spread_types:
            skipped = [r for r in (outputs.get("spread_report") or []) if not r.get("found")]
            message = f"{n_clips} of {len(spread_types)} requested clips ready"
            if skipped:
                message += " (" + "; ".join(r.get("reason", r["type"]) for r in skipped) + ")"
        else:
            message = f"{n_clips} clips ready"
        patch_job(job_id, {"status": "done", "progress": 100, "stage": "done",
                           "episode_id": result.get("episode_id"), "outputs": outputs,
                           "message": message})
    except (Exception, SystemExit) as e:
        # SystemExit included: it is a BaseException, so a bare `except Exception`
        # let it kill the container without ever writing the job's error, leaving
        # the job "running" until the stall watchdog expired it an hour later.
        patch_job(job_id, {"status": "error", "error": str(e)[:500] or "worker exited"})
        raise


# ----------------------------------------------------------------------
# action="video": render ONE 16:9 landscape audiogram for a window of an episode
# (KH-VRL-001 Wave 1). Drives progress through studio_video_jobs and uploads to
# the `studio-video` bucket. Audio only: no 1080p video fetch, no 35s clip cap.
# ----------------------------------------------------------------------
def master_source_kind(url):
    """Classify a master_url for process_youtube_upload: 'drive' for a Google Drive
    file link (gdown), 'https' for any other https URL (a signed Supabase Storage
    URL for a master uploaded from the producer's computer), None otherwise. Pure."""
    u = (url or "").strip()
    if _drive_file_id(u):
        return "drive"
    if u.lower().startswith("https://"):
        return "https"
    return None


def _master_extension(file_name):
    """File extension for the local copy of an uploaded master, from its original
    name; mp4 when unknown. Letters/digits only so a name can never form a path."""
    ext = ((file_name or "").rsplit(".", 1)[-1] if "." in (file_name or "") else "").lower()
    return ext if re.fullmatch(r"[a-z0-9]{2,5}", ext or "") else "mp4"


def _download_https_stream(url, dst, on_progress=None, chunk=8 * 1024 * 1024):
    """Stream an https URL to disk. 8 MB chunks keep memory flat on a multi-GB
    master; on_progress gets 0-100 when the server sends Content-Length."""
    import requests
    with requests.get(url, stream=True, timeout=(30, 300)) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        last = -1
        with open(dst, "wb") as f:
            for part in r.iter_content(chunk_size=chunk):
                if not part:
                    continue
                f.write(part)
                done += len(part)
                if total and on_progress:
                    pct = int(done * 100 / total)
                    if pct != last and pct % 10 == 0:
                        last = pct
                        on_progress(pct)
    return dst


class _ProgressReader:
    """File-like wrapper for a streaming HTTP body that reports bytes sent.

    requests keeps Content-Length (no chunked encoding, which YouTube's resumable
    PUT rejects) because __len__ answers the size; http.client then pulls the body
    through read() in blocks, so counting here is counting what left the socket
    buffer. `on_progress(done, total)` fires at most once per `step` percent so a
    multi-GB master produces ~20 row patches, not thousands. Pure apart from the
    callback, so it is unit tested with an in-memory file."""

    def __init__(self, fileobj, total, on_progress, step=5):
        self._f = fileobj
        self._total = int(total)
        self._cb = on_progress
        self._step = step
        self._done = 0
        self._last_bucket = 0  # 0..4% stays silent; the stage already shows 15%

    def __len__(self):
        return self._total

    def read(self, n=-1):
        chunk = self._f.read(n)
        if chunk:
            self._done += len(chunk)
            bucket = (self._done * 100 // self._total) // self._step if self._total else 0
            if bucket != self._last_bucket:
                self._last_bucket = bucket
                try:
                    self._cb(self._done, self._total)
                except Exception:
                    pass  # progress is cosmetic; never break the upload
        return chunk


def _drive_file_id(url):
    """Google Drive file id from a Drive link, or None (same regex as process_job)."""
    import re
    m = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?id=|.*[?&]id=)([A-Za-z0-9_-]{20,})", url or "")
    return m.group(1) if m else None


def _fetch_audio_window(url, start, end, out_path):
    """Pull ONLY the [start, end] audio window from YouTube (bestaudio + a section
    download, force_keyframes for accurate edges). Writes out_path (m4a)."""
    import sys
    sys.path.insert(0, "/root")
    import yt_dlp
    from src import ytdlp
    base = out_path[:-len(".m4a")] if out_path.endswith(".m4a") else out_path
    opts = {
        "format": "bestaudio/best",
        "outtmpl": base + ".%(ext)s",
        "download_ranges": yt_dlp.utils.download_range_func(None, [(float(start), float(end))]),
        "force_keyframes_at_cuts": True,       # exact in/out points
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}],
        "quiet": True, "no_warnings": True,
        **ytdlp.resilience_opts(),             # player-client + cookies bypass for cloud IPs
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    if not os.path.exists(out_path) or os.path.getsize(out_path) < 1000:
        raise RuntimeError("YouTube audio window download failed")
    return out_path


@app.function(image=image, timeout=1800, cpu=JOB_CPU, secrets=[SECRET, COOKIE_SECRET, XAI_SECRET])
def process_video_job(payload: dict):
    import subprocess
    import sys
    sys.path.insert(0, "/root")
    os.chdir("/root")
    _write_cookies()

    job_id = payload.get("job_id")

    def prog(stage, pct, msg=""):
        patch_video_job(job_id, {"status": "running", "stage": stage,
                                 "progress": int(pct), "message": msg})

    try:
        # kh-studio has a 45s stall watchdog on 'queued', so go running immediately.
        prog("fetch", 5, "Fetching audio for the selected window")

        # Defensive re-validation (the endpoint already 400s bad payloads).
        err = validate_video_payload(payload)
        if err:
            raise RuntimeError(f"invalid job payload: {err}")
        url = payload["url"]
        start, end = int(payload["start_sec"]), int(payload["end_sec"])

        os.makedirs("/tmp/vjob", exist_ok=True)
        audio_path = "/tmp/vjob/window.m4a"

        file_id = _drive_file_id(url)
        if file_id:
            # Google Drive master: download, then extract just the window's audio.
            prog("fetch", 8, "Downloading the master from Google Drive")
            import gdown
            src = f"/tmp/vjob/{file_id}.mp4"
            gdown.download(id=file_id, output=src, quiet=True)
            if not os.path.exists(src) or os.path.getsize(src) < 10000:
                raise RuntimeError("Drive download failed. Is the file shared 'anyone with the link'?")
            prog("fetch", 20, "Extracting the audio window")
            r = subprocess.run(
                ["ffmpeg", "-y", "-ss", str(start), "-to", str(end), "-i", src,
                 "-vn", "-ac", "2", "-c:a", "aac", audio_path],
                capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"audio extract failed: {r.stderr[-400:]}")
        else:
            # YouTube: pull only the window's audio, never the 1080p video.
            prog("fetch", 10, "Pulling the audio window from YouTube")
            _fetch_audio_window(url, start, end, audio_path)

        prog("render", 30, "Rendering the landscape audiogram")
        from src import audiogram

        words = None
        transcript = payload.get("transcript")
        if isinstance(transcript, dict) and transcript.get("words"):
            words = audiogram.window_words(transcript["words"], start, end) or None
        title = transcript.get("title") if isinstance(transcript, dict) else None

        out_path = "/tmp/vjob/audiogram_landscape.mp4"
        _, notes = audiogram.render_landscape(
            audio_path, out_path, series=payload.get("series"),
            brand=payload.get("brand"), words=words, title=title,
            guest_name=payload.get("guest_name"))

        prog("upload", 90, "Uploading the finished video")
        storage_path = upload_file(out_path, f"{job_id}/audiogram_landscape.mp4",
                                   bucket=VIDEO_BUCKET)
        msg = "Ready for review"
        if notes:
            msg += " (" + "; ".join(notes) + ")"
        patch_video_job(job_id, {"status": "review", "stage": "done", "progress": 100,
                                 "output_url": storage_path, "message": msg})
    except Exception as e:
        patch_video_job(job_id, {"status": "error", "error": str(e)[:500]})
        raise


# ----------------------------------------------------------------------
# action="audiogram" (KH-AUD-001): a standalone LONGER-FORM branded audiogram
# (square + vertical), picked automatically by a clip_type lens (KH-CTP-001,
# reused unchanged) + a duration preset — contrast with action="video", whose
# landscape promo takes a manually chosen start/end window. Audio only: no
# 1080p video fetch, no face detection/reframe, no Shorts 35s ceiling — reuses
# detect.py's transcript-only moment picking with a widened band (see
# detect.audiogram_band_override) so the SAME "which moment matters" engine
# Shorts uses just runs to a longer target length. Reuses the `studio-video`
# bucket (same private, human-review-then-approve shape as action="video";
# no new bucket needed) and drives progress through studio_audiogram_jobs.
# ----------------------------------------------------------------------
AUDIOGRAM_DURATION_SEC = {30, 60, 90, 120}


def validate_audiogram_payload(payload):
    """Validate an action="audiogram" payload. Returns None when valid, else a
    short human-readable reason (the endpoint turns it into a 400). Pure stdlib,
    mirrors validate_video_payload's shape. `clip_type` is validated separately
    at the endpoint (it needs detect.TYPE_PROFILES, matching how action="generate"
    validates it) rather than imported here."""
    if not isinstance(payload, dict):
        return "payload must be a JSON object"
    for field in ("job_id", "url", "series"):
        v = payload.get(field)
        if not isinstance(v, str) or not v.strip():
            return f"missing {field}"
    duration_sec = payload.get("duration_sec", 60)
    if isinstance(duration_sec, bool) or duration_sec not in AUDIOGRAM_DURATION_SEC:
        return (f"duration_sec must be one of {sorted(AUDIOGRAM_DURATION_SEC)} "
                f"(got {duration_sec!r})")
    transcript = payload.get("transcript")
    if not isinstance(transcript, dict) or not transcript.get("words"):
        return "transcript with word timings is required (clip_type selection runs on it)"
    return None


@app.function(image=image, timeout=1800, cpu=JOB_CPU, secrets=[SECRET, COOKIE_SECRET, XAI_SECRET])
def process_audiogram_job(payload: dict):
    import subprocess
    import sys
    sys.path.insert(0, "/root")
    os.chdir("/root")
    _write_cookies()

    job_id = payload.get("job_id")

    def prog(stage, pct, msg=""):
        patch_audiogram_job(job_id, {"status": "running", "stage": stage,
                                     "progress": int(pct), "message": msg})

    try:
        # kh-studio has a stall watchdog on 'queued', so go running immediately.
        prog("select", 5, "Picking the moment for this audiogram")

        # Defensive re-validation (the endpoint already 400s bad payloads).
        err = validate_audiogram_payload(payload)
        if err:
            raise RuntimeError(f"invalid job payload: {err}")

        url = payload["url"]
        series = payload.get("series")
        guest_name = payload.get("guest_name")
        duration_sec = int(payload.get("duration_sec") or 60)
        transcript = payload["transcript"]

        from src import detect

        clip_type = str(payload.get("clip_type") or "best")
        if clip_type not in detect.TYPE_PROFILES:
            clip_type = "best"                 # unknown type degrades to current behaviour
        band_override = detect.audiogram_band_override(duration_sec)

        os.makedirs("/tmp/ajob", exist_ok=True)
        tpath = "/tmp/ajob/transcript.json"
        import json as _json
        with open(tpath, "w") as f:
            _json.dump(transcript, f)

        # Moment picking runs on the TRANSCRIPT alone (no download needed yet) —
        # the same engine Shorts uses, just with a widened duration band.
        result = detect.detect(tpath, use_llm=True, top_n=1, clip_type=clip_type,
                               band_override=band_override,
                               usage_ctx={"job_id": job_id, "source": "worker-audiogram"})
        picks = result.get("clips") or []
        if not picks:
            raise RuntimeError(
                f"no {duration_sec}s moment found for clip_type={clip_type!r} in this episode "
                f"({result.get('n_candidates', 0)} candidates, {result.get('n_passed_gate', 0)} "
                f"passed the gate) — try a shorter duration or a different clip type")
        pick = picks[0]
        start, end = float(pick["start"]), float(pick["end"])

        prog("fetch", 20, "Fetching the audio for the selected moment")
        audio_path = "/tmp/ajob/window.m4a"
        file_id = _drive_file_id(url)
        if file_id:
            # Google Drive master: download, then extract just the window's audio.
            prog("fetch", 25, "Downloading the master from Google Drive")
            import gdown
            src = f"/tmp/ajob/{file_id}.mp4"
            gdown.download(id=file_id, output=src, quiet=True)
            if not os.path.exists(src) or os.path.getsize(src) < 10000:
                raise RuntimeError("Drive download failed. Is the file shared 'anyone with the link'?")
            prog("fetch", 35, "Extracting the audio window")
            r = subprocess.run(
                ["ffmpeg", "-y", "-ss", str(start), "-to", str(end), "-i", src,
                 "-vn", "-ac", "2", "-c:a", "aac", audio_path],
                capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"audio extract failed: {r.stderr[-400:]}")
        else:
            # YouTube: pull only the window's audio, never the 1080p video.
            prog("fetch", 30, "Pulling the audio window from YouTube")
            _fetch_audio_window(url, start, end, audio_path)

        prog("render", 55, "Rendering the audiogram (square + vertical)")
        from src import audiogram
        words = audiogram.window_words(transcript.get("words") or [], start, end)
        title = transcript.get("title")
        out_base = "/tmp/ajob/audiogram"
        sq, vt = audiogram.render(
            audio_path, words, out_base, series=series,
            caption=pick.get("hook_line"), title=title, guest_name=guest_name,
            timed_captions=True)

        prog("upload", 90, "Uploading the finished audiograms")
        sq_url = upload_file(sq, f"{job_id}/audiogram_square.mp4", bucket=VIDEO_BUCKET)
        vt_url = upload_file(vt, f"{job_id}/audiogram_vertical.mp4", bucket=VIDEO_BUCKET)

        patch_audiogram_job(job_id, {
            "status": "review", "stage": "done", "progress": 100,
            "moment_start_sec": int(round(start)), "moment_end_sec": int(round(end)),
            "output_square_url": sq_url, "output_vertical_url": vt_url,
            "message": f"{duration_sec}s {clip_type} audiogram ready for review",
        })
    except Exception as e:
        patch_audiogram_job(job_id, {"status": "error", "error": str(e)[:500]})
        raise


# ----------------------------------------------------------------------
# action="episode_qc": mechanical QC on a finished episode master (KH-QC-001).
# Runs on the Google Drive file that is ABOUT to be published, not on something
# already live, and writes its findings straight back to Supabase. Progress and
# the terminal state go to episode_qc_runs, the findings to episode_qc_checks.
# The worker sends machine facts only; the volunteer-facing wording lives in
# kh-studio (lib/studio/qc-types.ts) so it can change without a redeploy.
# ----------------------------------------------------------------------
QC_RUNS_TABLE = "episode_qc_runs"
QC_CHECKS_TABLE = "episode_qc_checks"

# Checks that need word-level text. Re-transcribing a 60 minute master is a real
# per-episode STT charge, so it only happens when one of these is actually in play.
QC_TRANSCRIPT_CHECKS = {"no_go_topic", "duplicate_segment", "mid_word_cut",
                        "transcript_mismatch"}

# One duplicated take is worth a finding; forty of them is a wall nobody reads.
# Past this, the rest are rolled into a single "and N more" line.
QC_MAX_FINDINGS_PER_CHECK = 25

# Silence is detected from 0.6s (ordinary speech rhythm above that), but only a
# real hole gets a warning: a conversation about hard things is full of pauses,
# and flagging every one of them would train people to skim the whole report.
QC_SILENCE_WARN_SEC = 2.0
QC_CLIP_PEAK_DB = -0.1           # at or above this is on the ceiling
QC_AV_START_TOL_SEC = 0.15       # lip sync goes visibly wrong past about this
QC_AV_DURATION_TOL_SEC = 1.0     # picture and sound should end together
QC_CAPTIONS_TOL_SEC = 2.0        # captions ending this far off the picture
QC_DURATION_TOL_PCT = 0.02       # expected-vs-actual runtime slack
QC_DURATION_TOL_MIN_SEC = 5.0


def validate_episode_qc_payload(payload):
    """Validate an action="episode_qc" payload. Returns None when valid, else a
    short human-readable reason (the endpoint turns it into a 400). Pure stdlib,
    same shape as validate_video_payload / validate_audiogram_payload. The
    `checks` names are validated at the endpoint (they need src/qc.py's
    CHECK_TYPES), matching the split the other actions already use."""
    import re
    if not isinstance(payload, dict):
        return "payload must be a JSON object"
    # episode_id is required, not optional: episode_qc_checks.studio_episode_id
    # is NOT NULL in kh-studio (db/293), so a findings POST without it would be
    # rejected wholesale and the run would report an error instead of a result.
    for field in ("job_id", "url", "episode_id"):
        v = payload.get(field)
        if not isinstance(v, str) or not v.strip():
            return f"missing {field}"
    url = payload["url"].strip()
    if not _drive_file_id(url):
        # QC has to run on the file the upload path actually sends to YouTube.
        # A YouTube link is a different artefact (already published, re-encoded
        # by YouTube), so a clean result on it would say nothing about the master.
        if re.search(r"youtu\.?be", url, re.IGNORECASE):
            return ("url must be the Google Drive master, not a YouTube link: QC runs "
                    "on the file about to be published, not on one already published")
        return "url must be a Google Drive file link (the episode master)"
    if payload.get("expected") is not None and not isinstance(payload["expected"], dict):
        return "expected must be an object"
    terms = payload.get("no_go_terms")
    if terms is not None:
        if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
            return "no_go_terms must be a list of strings"
    if payload.get("transcript") is not None and not isinstance(payload["transcript"], dict):
        return "transcript must be an object"
    if payload.get("utterances") is not None and not isinstance(payload["utterances"], list):
        return "utterances must be a list"
    checks = payload.get("checks")
    if checks is not None:
        if not isinstance(checks, list) or not all(isinstance(c, str) for c in checks):
            return "checks must be a list of strings"
    return None


def patch_qc_run(run_id, fields):
    """PATCH an episode_qc_runs row (service role bypasses RLS). Never raises.
    Columns: status, stage, progress, message, error, source_checksum,
    source_bytes, media_duration_sec, transcript_source, error_count,
    warning_count, info_count, finished_at."""
    import json as _json
    import requests
    try:
        body = _json.dumps(fields, ensure_ascii=True)
        r = requests.patch(
            f"{_sb_url()}/rest/v1/{QC_RUNS_TABLE}",
            headers=_sb_headers({"Content-Type": "application/json",
                                 "Prefer": "return=minimal"}),
            params={"id": f"eq.{run_id}"},
            data=body, timeout=30,
        )
        if r.status_code >= 300:
            print(f"patch_qc_run {r.status_code}: {r.text[:200]}")
    except Exception as e:                       # progress is best-effort
        print(f"patch_qc_run failed: {e}")


# Findings go up in batches rather than one giant array. A long episode can
# produce a lot of rows, and a single request big enough to be rejected or time
# out would lose the whole run's result over a size limit nobody would think to
# look for.
QC_FINDINGS_BATCH = 250


def post_qc_findings(rows):
    """POST the findings to episode_qc_checks. Returns (ok, reason).

    The REASON is load-bearing, not decoration. The first real run of this
    worker failed here and reported only "the findings could not be saved",
    which is true, useless, and cost a full re-run to learn nothing. Whatever
    PostgREST says goes back onto the run so the next person reads the actual
    cause instead of guessing at it.

    A run that lost its findings must never report "complete": that would show
    a clean bill of health for an episode nobody actually checked. The caller
    errors the run instead."""
    if not rows:
        return True, None
    import json as _json
    import requests
    for i in range(0, len(rows), QC_FINDINGS_BATCH):
        batch = rows[i:i + QC_FINDINGS_BATCH]
        try:
            r = requests.post(
                f"{_sb_url()}/rest/v1/{QC_CHECKS_TABLE}",
                headers=_sb_headers({"Content-Type": "application/json",
                                     "Prefer": "return=minimal"}),
                data=_json.dumps(batch, ensure_ascii=True), timeout=120,
            )
            if r.status_code >= 300:
                reason = f"HTTP {r.status_code}: {(r.text or '').strip()[:400]}"
                print(f"post_qc_findings {reason}")
                return False, reason
        except Exception as e:
            reason = f"{type(e).__name__}: {str(e)[:300]}"
            print(f"post_qc_findings failed: {reason}")
            return False, reason
    return True, None


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


@app.function(image=image, timeout=3600, cpu=JOB_CPU,
              secrets=[SECRET, COOKIE_SECRET, XAI_SECRET])
def process_episode_qc_job(payload: dict):
    import subprocess
    import sys
    from datetime import datetime, timezone
    sys.path.insert(0, "/root")
    os.chdir("/root")

    job_id = payload.get("job_id")
    episode_id = payload.get("episode_id")
    workdir = "/tmp/qcjob"
    src_path = None
    audio_path = None

    def prog(stage, pct, msg=""):
        patch_qc_run(job_id, {"status": "running", "stage": stage,
                              "progress": int(pct), "message": msg})

    try:
        # kh-studio has a 45s queued-stall watchdog, so go running immediately.
        prog("queued", 0, "Starting the QC pass")

        # Defensive re-validation (the endpoint already 400s bad payloads).
        err = validate_episode_qc_payload(payload)
        if err:
            raise RuntimeError(f"invalid job payload: {err}")

        from src import qc, qc_transcript

        expected = payload.get("expected") or {}
        no_go_terms = payload.get("no_go_terms") or []
        transcript = payload.get("transcript") or {}
        utterances = payload.get("utterances") or []
        wanted = {c for c in (payload.get("checks") or []) if c in qc.CHECK_TYPES}

        findings = []
        cut_candidates = []

        def runs(check_type):
            """An absent `checks` list means run everything the inputs allow."""
            return not wanted or check_type in wanted

        def add(check_type, severity, detail, start=None, end=None):
            if runs(check_type):
                findings.append(qc.make_finding(job_id, episode_id, check_type,
                                                severity, detail, start, end))

        def add_capped(check_type, items, render):
            """Emit at most QC_MAX_FINDINGS_PER_CHECK findings, then one info row
            saying how many were not listed. A truncated list that does not say
            it was truncated is a lie about how clean the episode is."""
            for item in items[:QC_MAX_FINDINGS_PER_CHECK]:
                detail, start, end = render(item)
                add(check_type, "warning", detail, start, end)
            extra = len(items) - QC_MAX_FINDINGS_PER_CHECK
            if extra > 0:
                add(check_type, "info", f"{_plural(extra, 'further finding')} of this "
                                        f"type, not listed individually")

        # ---- the master itself ---------------------------------------
        file_id = _drive_file_id(payload["url"])
        prog("download", 5, "Downloading the master from Google Drive")
        import gdown
        os.makedirs(workdir, exist_ok=True)
        src_path = f"{workdir}/{file_id}.mp4"
        gdown.download(id=file_id, output=src_path, quiet=True)
        if not os.path.exists(src_path) or os.path.getsize(src_path) < 10000:
            raise RuntimeError("Drive download failed. Is the file shared 'anyone with the link'?")
        source_bytes = os.path.getsize(src_path)

        prog("identity", 10, "Checksumming the file")
        checksum = qc.md5_file(src_path)
        if checksum:
            add("file_identity", "info",
                f"md5 {checksum}, {source_bytes / (1024 ** 3):.2f} GB")
        else:
            add("file_identity", "info", "The file could not be checksummed, so this "
                                         "result cannot be tied to one exact export")

        # ---- container and streams -----------------------------------
        prog("probe", 15, "Reading the file's streams")
        probe = qc.probe_media(src_path)
        media_duration = probe.get("duration_sec")
        video, audio = probe.get("video"), probe.get("audio")

        if not probe:
            add("export_settings", "info", "ffprobe could not read this file, so every "
                                           "measurement below was skipped")
        else:
            parts = []
            if video:
                parts.append(f"{video.get('width')}x{video.get('height')} "
                             f"{video.get('codec')} at {video.get('fps')} fps")
            if audio:
                parts.append(f"{audio.get('codec')} {audio.get('sample_rate')} Hz "
                             f"{audio.get('channels')} ch")
            parts.append(f"container {probe.get('container')}")
            summary = ", ".join(str(p) for p in parts)
            # Only objectively broken exports warn here. There is no committed KH
            # export standard for this worker to measure against, and inventing
            # one would produce confident warnings about a correct file.
            problems = []
            if not video:
                problems.append("no video stream")
            if not audio:
                problems.append("no audio stream")
            elif audio.get("sample_rate") and audio["sample_rate"] < 44100:
                problems.append(f"audio sample rate is {audio['sample_rate']} Hz, "
                                f"below 44.1 kHz")
            if problems:
                add("export_settings", "warning", f"{'; '.join(problems)}. Export: {summary}")
            else:
                add("export_settings", "info", f"Export: {summary}")

        # ---- runtime -------------------------------------------------
        if media_duration is None:
            add("duration", "info", "The media duration could not be read, so the "
                                    "runtime was not checked")
            add("episode_length", "info", "The media duration could not be read, so the "
                                          "episode length was not checked")
        else:
            exp_duration = expected.get("duration_sec")
            if exp_duration:
                delta = media_duration - float(exp_duration)
                tol = max(QC_DURATION_TOL_MIN_SEC,
                          float(exp_duration) * QC_DURATION_TOL_PCT)
                if abs(delta) > tol:
                    add("duration", "warning",
                        f"The master runs {qc.timecode(media_duration)}, "
                        f"{delta:+.0f}s against the expected {qc.timecode(exp_duration)}")
                else:
                    add("duration", "info",
                        f"The master runs {qc.timecode(media_duration)}, within "
                        f"{delta:+.0f}s of expected")
            else:
                add("duration", "info", f"The master runs {qc.timecode(media_duration)}. "
                                        f"No expected duration was supplied, so nothing "
                                        f"was compared")
            low, high = expected.get("length_min_sec"), expected.get("length_max_sec")
            if low is None and high is None:
                add("episode_length", "info", "No length range was supplied, so the "
                                              "episode length was not checked")
            elif low is not None and media_duration < float(low):
                add("episode_length", "warning",
                    f"{qc.timecode(media_duration)} is shorter than the "
                    f"{qc.timecode(low)} minimum for an episode")
            elif high is not None and media_duration > float(high):
                add("episode_length", "warning",
                    f"{qc.timecode(media_duration)} is longer than the "
                    f"{qc.timecode(high)} maximum for an episode")
            else:
                add("episode_length", "info",
                    f"{qc.timecode(media_duration)} sits inside the expected range")

        # ---- picture -------------------------------------------------
        if runs("black_frame"):
            prog("video", 25, "Looking for black frames")
            ok, _, stderr = qc.run_capture(qc.build_blackdetect_command(src_path))
            blacks = qc.parse_blackdetect(stderr)
            cut_candidates.extend([t for pair in blacks for t in pair])
            if not ok:
                add("black_frame", "info", "ffmpeg could not scan this file for black "
                                           "frames, so it was not checked")
            elif not blacks:
                add("black_frame", "info", f"No black run of {qc.BLACK_MIN_DUR}s or "
                                           f"longer was found")
            else:
                add_capped("black_frame", blacks, lambda b: (
                    f"{b[1] - b[0]:.1f}s of black at {qc.timecode(b[0])}", b[0], b[1]))

        if runs("freeze_frame"):
            prog("video", 35, "Looking for frozen picture")
            ok, _, stderr = qc.run_capture(qc.build_freezedetect_command(src_path))
            freezes = qc.parse_freezedetect(stderr)
            cut_candidates.extend([t for pair in freezes for t in pair if t is not None])
            if not ok:
                add("freeze_frame", "info", "ffmpeg could not scan this file for frozen "
                                            "picture, so it was not checked")
            elif not freezes:
                add("freeze_frame", "info", f"No frozen picture of {qc.FREEZE_MIN_DUR}s "
                                            f"or longer was found")
            else:
                add_capped("freeze_frame", freezes, lambda f: (
                    (f"Picture frozen from {qc.timecode(f[0])} to the end of the file"
                     if f[1] is None else
                     f"{f[1] - f[0]:.1f}s of frozen picture at {qc.timecode(f[0])}"),
                    f[0], f[1]))

        if runs("resolution"):
            prog("video", 45, "Sampling the picture size across the episode")
            if not probe:
                add("resolution", "info", "The file could not be probed, so picture size "
                                          "changes were not checked")
            else:
                changes = qc.detect_resolution_changes(src_path, duration_sec=media_duration)
                if changes:
                    add_capped("resolution", changes, lambda c: (
                        f"Picture is {c['width']}x{c['height']} at {c['fps']} fps at "
                        f"{qc.timecode(c['time'])}, against {c['baseline_width']}x"
                        f"{c['baseline_height']} at {c['baseline_fps']} fps earlier",
                        c["time"], None))
                elif video:
                    add("resolution", "info",
                        f"Picture stayed {video.get('width')}x{video.get('height')} "
                        f"across the sampled points")
                else:
                    add("resolution", "info", "This file has no video stream to sample")

        # ---- sound ---------------------------------------------------
        if runs("silence_gap"):
            prog("audio", 55, "Listening for dead air")
            ok, _, stderr = qc.run_capture(qc.build_silencedetect_command(src_path))
            silences = qc.parse_silencedetect(stderr)
            if not ok:
                add("silence_gap", "info", "ffmpeg could not scan this file for silence, "
                                           "so it was not checked")
            else:
                long_gaps = [(s, e) for s, e in silences
                             if e is not None and e - s >= QC_SILENCE_WARN_SEC]
                short = len(silences) - len(long_gaps)
                if long_gaps:
                    add_capped("silence_gap", long_gaps, lambda g: (
                        f"{g[1] - g[0]:.1f}s of near-silence at {qc.timecode(g[0])}",
                        g[0], g[1]))
                else:
                    add("silence_gap", "info", f"No silence of {QC_SILENCE_WARN_SEC}s or "
                                               f"longer was found")
                if short > 0:
                    add("silence_gap", "info",
                        f"{_plural(short, 'shorter pause')} under {QC_SILENCE_WARN_SEC}s "
                        f"measured and not flagged, since a held pause is part of how "
                        f"these conversations sound")

        if runs("loudness"):
            prog("audio", 62, "Measuring loudness")
            ok, _, stderr = qc.run_capture(qc.build_ebur128_command(src_path))
            measured = qc.parse_ebur128(stderr)
            integrated = measured.get("integrated_lufs")
            true_peak = measured.get("true_peak_dbtp")
            if not ok or integrated is None:
                add("loudness", "info", "Loudness could not be measured on this file, so "
                                        "it was not checked")
            else:
                target = expected.get("loudness_lufs")
                tolerance = float(expected.get("loudness_tolerance") or 2.0)
                reading = (f"{integrated:.1f} LUFS integrated, true peak "
                           f"{true_peak if true_peak is None else round(true_peak, 1)} dBTP, "
                           f"LRA {measured.get('lra')} LU")
                if target is None:
                    add("loudness", "info", f"Measured {reading}. No target was supplied, "
                                            f"so nothing was compared")
                elif abs(integrated - float(target)) > tolerance:
                    add("loudness", "warning",
                        f"Measured {reading}, against a target of {float(target):.1f} LUFS "
                        f"plus or minus {tolerance:.1f}")
                else:
                    add("loudness", "info", f"Measured {reading}, inside the "
                                            f"{float(target):.1f} LUFS target")
                ceiling = expected.get("true_peak_dbtp")
                if true_peak is not None and ceiling is not None and true_peak > float(ceiling):
                    add("loudness", "warning",
                        f"True peak is {true_peak:.1f} dBTP, over the "
                        f"{float(ceiling):.1f} dBTP ceiling")

        if runs("clipping"):
            prog("audio", 68, "Checking for clipping")
            ok, _, stderr = qc.run_capture(qc.build_astats_command(src_path))
            stats = qc.parse_astats(stderr)
            peak = stats.get("peak_level_db")
            if not ok or peak is None:
                add("clipping", "info", "The sample peak could not be measured, so "
                                        "clipping was not checked")
            elif peak >= QC_CLIP_PEAK_DB:
                add("clipping", "warning",
                    f"Sample peak is {peak:.2f} dBFS, sitting on the ceiling "
                    f"({stats.get('peak_count')} samples at peak)")
            else:
                add("clipping", "info", f"Sample peak is {peak:.2f} dBFS, clear of the "
                                        f"ceiling")

        if runs("av_sync"):
            drift = qc.av_sync_drift(src_path, probe=probe)
            if not drift:
                add("av_sync", "info", "The video and audio stream timings could not both "
                                       "be read, so A/V sync was not checked")
            else:
                offset = drift.get("start_offset_sec")
                delta = drift.get("duration_delta_sec")
                problems = []
                if offset is not None and abs(offset) > QC_AV_START_TOL_SEC:
                    problems.append(f"picture starts {offset:+.3f}s from the audio")
                if delta is not None and abs(delta) > QC_AV_DURATION_TOL_SEC:
                    problems.append(f"picture runs {delta:+.2f}s against the audio")
                if problems:
                    add("av_sync", "warning", "; ".join(problems))
                else:
                    add("av_sync", "info", f"Picture and audio start within "
                                           f"{QC_AV_START_TOL_SEC}s and end together")

        # ---- the transcript ------------------------------------------
        prog("transcript", 75, "Checking the transcript against the master")
        words = transcript.get("words") or []
        transcript_source = "none"
        needs_transcript = (not wanted) or bool(wanted & QC_TRANSCRIPT_CHECKS)

        def _retranscribe():
            """Re-transcribe the master when the stored transcript belongs to a
            different edit. This is a real STT charge per episode, so it only
            runs when a transcript-dependent check is actually in play, and any
            failure degrades to 'none' with an honest note rather than to a
            transcript that does not match the file."""
            nonlocal audio_path
            if not os.environ.get("XAI_API_KEY"):
                add("transcript_mismatch", "info", "No transcription key is set on the "
                                                   "worker, so the transcript checks did "
                                                   "not run")
                return [], "none"
            prog("transcribe", 78, "Re-transcribing the master")
            audio_path = f"{workdir}/qc_audio.wav"
            r = subprocess.run(
                ["ffmpeg", "-y", "-nostdin", "-i", src_path, "-vn", "-ac", "1",
                 "-ar", "16000", audio_path], capture_output=True, text=True)
            if r.returncode != 0 or not os.path.exists(audio_path):
                add("transcript_mismatch", "info", "Audio could not be extracted for "
                                                   "re-transcription, so the transcript "
                                                   "checks did not run")
                return [], "none"
            from src import transcribe as stt
            try:
                out = stt.transcribe(
                    audio_path, provider="grok",
                    usage_ctx={"job_id": job_id, "source": "worker-qc",
                               "episode_ref": episode_id or file_id})
            except Exception as e:
                add("transcript_mismatch", "info",
                    f"Re-transcribing the master failed ({str(e)[:160]}), so the "
                    f"transcript checks did not run")
                return [], "none"
            return out.get("words") or [], "re_transcribed"

        if words and qc_transcript.transcript_fits_media(words, media_duration):
            transcript_source = "stored"
            if media_duration is None:
                add("transcript_mismatch", "info",
                    "The media duration could not be read, so the stored transcript was "
                    "used without being checked against the file")
            else:
                add("transcript_mismatch", "info",
                    f"The stored transcript ends at "
                    f"{qc.timecode(qc_transcript.last_word_end(words))} and fits this "
                    f"{qc.timecode(media_duration)} master")
        elif words:
            add("transcript_mismatch", "warning",
                f"The stored transcript ends at "
                f"{qc.timecode(qc_transcript.last_word_end(words))} but the master runs "
                f"{qc.timecode(media_duration)}, so it belongs to a different edit")
            if needs_transcript:
                words, transcript_source = _retranscribe()
                # Diarised turns came from the stored transcript's edit, so they
                # no longer describe this file.
                utterances = []
        elif needs_transcript:
            add("transcript_mismatch", "info", "No stored transcript was supplied with "
                                               "this run")
            words, transcript_source = _retranscribe()

        # ---- consent: the one check that blocks a publish -------------
        if runs("no_go_topic"):
            prog("consent", 85, "Scanning for no-go topics")
            if not no_go_terms:
                add("no_go_topic", "info", "No no-go terms are recorded for this hero, so "
                                           "nothing was scanned for")
            elif not words:
                add("no_go_topic", "info", "No transcript was available for this master, "
                                           "so the no-go topic scan did not run")
            else:
                hits = qc_transcript.no_go_hits(words, no_go_terms)
                if not hits:
                    add("no_go_topic", "info",
                        f"Scanned {len(words)} words against "
                        f"{_plural(len(no_go_terms), 'no-go term')} with no match")
                else:
                    for hit in hits[:QC_MAX_FINDINGS_PER_CHECK]:
                        add("no_go_topic", "error",
                            f"A no-go topic is spoken at {qc.timecode(hit['start'])}: "
                            f"\"{hit['text'][:120]}\"", hit["start"], hit["end"])
                    extra = len(hits) - QC_MAX_FINDINGS_PER_CHECK
                    if extra > 0:
                        add("no_go_topic", "error",
                            f"{_plural(extra, 'further no-go match')} found, not listed "
                            f"individually")

        # ---- edit quality --------------------------------------------
        if runs("mid_word_cut"):
            explicit = [c for c in (payload.get("cut_points") or [])
                        if isinstance(c, (int, float)) and not isinstance(c, bool)]
            # Without an edit decision list, the only cut points the worker can
            # actually see are the visible splice markers: the edges of a black
            # run or a freeze. Silence edges are deliberately excluded, since a
            # cut in silence is a clean cut by definition.
            points = explicit or sorted(set(cut_candidates))
            if not words:
                add("mid_word_cut", "info", "No transcript was available for this master, "
                                            "so cut points were not checked")
            elif not points:
                add("mid_word_cut", "info", "No cut points were supplied or detected in "
                                            "this master, so nothing was checked")
            else:
                cuts = qc_transcript.mid_word_cuts(words, points)
                if cuts:
                    add_capped("mid_word_cut", cuts, lambda c: (
                        f"A cut at {qc.timecode(c['time'])} lands inside the word "
                        f"\"{c['word']}\"", c["start"], c["end"]))
                else:
                    add("mid_word_cut", "info",
                        f"Checked {_plural(len(points), 'cut point')}, none land inside "
                        f"a word")

        if runs("duplicate_segment"):
            if not words:
                add("duplicate_segment", "info", "No transcript was available for this "
                                                 "master, so repeated takes were not "
                                                 "checked")
            else:
                dups = qc_transcript.duplicate_segments(words)
                if dups:
                    add_capped("duplicate_segment", dups, lambda d: (
                        f"\"{d['phrase'][:120]}\" is said at "
                        f"{qc.timecode(d['first_start'])} and again at "
                        f"{qc.timecode(d['second_start'])}",
                        d["second_start"], d["second_end"]))
                else:
                    add("duplicate_segment", "info", "No phrase of six or more words "
                                                     "repeats inside the search window")

        # ---- who is in the room, and the episode's shape --------------
        if runs("speaker_count"):
            if not utterances:
                add("speaker_count", "info", "No diarised turns were supplied for this "
                                             "master, so the speaker count was not checked")
            else:
                speakers = qc_transcript.speaker_count(utterances)
                expected_speakers = expected.get("speakers")
                if expected_speakers is None:
                    add("speaker_count", "info", f"{_plural(speakers, 'distinct speaker')} "
                                                 f"across the episode. No expected count "
                                                 f"was supplied")
                elif speakers != int(expected_speakers):
                    add("speaker_count", "warning",
                        f"{_plural(speakers, 'distinct speaker')} across the episode, "
                        f"against {int(expected_speakers)} expected")
                else:
                    add("speaker_count", "info",
                        f"{_plural(speakers, 'distinct speaker')}, as expected")

        if runs("segment_order"):
            if not utterances:
                add("segment_order", "info", "No diarised turns were supplied for this "
                                             "master, so the episode shape was not checked")
            else:
                shape = qc_transcript.segment_order(utterances)
                notes = []
                if shape["missing"]:
                    notes.append("did not find " + ", ".join(shape["missing"]))
                notes.extend(shape["out_of_order"])
                # Keyword heuristic, and it says so: a host who words the advisory
                # differently reads as missing, which is a prompt to look, not a fault.
                if notes:
                    add("segment_order", "warning",
                        f"Keyword check of the KH episode shape: {'; '.join(notes)}. This "
                        f"is a heuristic over {shape['turns']} turns, not a verdict")
                else:
                    add("segment_order", "info",
                        f"Hook, branded intro, content advisory, main conversation and "
                        f"outro all appear in order across {shape['turns']} turns, by "
                        f"keyword match")

        # ---- captions -------------------------------------------------
        if runs("captions_sync"):
            captions_duration = expected.get("captions_duration_sec")
            if captions_duration is None:
                add("captions_sync", "info", "No caption duration was supplied, so the "
                                             "captions were not checked against the file")
            elif media_duration is None:
                add("captions_sync", "info", "The media duration could not be read, so the "
                                             "captions were not checked against the file")
            else:
                delta = float(captions_duration) - media_duration
                if abs(delta) > QC_CAPTIONS_TOL_SEC:
                    add("captions_sync", "warning",
                        f"The captions end at {qc.timecode(captions_duration)}, "
                        f"{delta:+.1f}s from the {qc.timecode(media_duration)} master")
                else:
                    add("captions_sync", "info",
                        f"The captions end within {abs(delta):.1f}s of the master")

        # ---- write it all back ----------------------------------------
        prog("writing", 95, "Saving the findings")
        counts = {"error": 0, "warning": 0, "info": 0}
        for f in findings:
            counts[f["severity"]] = counts.get(f["severity"], 0) + 1
        saved, why = post_qc_findings(findings)
        if not saved:
            raise RuntimeError(
                f"The {len(findings)} findings could not be saved, so this run has no "
                f"result. The database said: {why}")

        if counts["error"]:
            message = (f"{_plural(counts['error'], 'blocking issue')}, "
                       f"{_plural(counts['warning'], 'warning')}")
        elif counts["warning"]:
            message = f"{_plural(counts['warning'], 'warning')}, nothing blocking"
        else:
            message = "Nothing flagged"

        patch_qc_run(job_id, {
            "status": "complete", "stage": "done", "progress": 100,
            "source_checksum": checksum, "source_bytes": source_bytes,
            "media_duration_sec": round(media_duration, 2) if media_duration else None,
            "transcript_source": transcript_source,
            "error_count": counts["error"], "warning_count": counts["warning"],
            "info_count": counts["info"], "message": message,
            "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    except (Exception, SystemExit) as e:
        # SystemExit included: it is a BaseException, so a bare `except Exception`
        # lets Modal kill the container without ever writing the run's error,
        # leaving it stuck on 'running' until a watchdog expires it. That bug
        # already happened once in process_job.
        patch_qc_run(job_id, {"status": "error", "error": str(e)[:500] or "worker exited"})
        raise
    finally:
        # The master is hero content under a signed release. It never stays on
        # the container after the pass, whether the pass worked or not.
        for path in (src_path, audio_path):
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except OSError as e:
                print(f"qc cleanup failed for {path}: {e}")


# ----------------------------------------------------------------------
# Per-clip op: the kh-studio "reframe" / "replace" buttons. Re-render ONE clip of a
# finished job, driving progress through outputs.clips[i].clip_job and NEVER touching
# the row `status` (it stays 'done' so the results view doesn't collapse).
# ----------------------------------------------------------------------
@app.function(image=image, timeout=900, cpu=JOB_CPU, min_containers=1,
             secrets=[SECRET, COOKIE_SECRET, XAI_SECRET, ANTHROPIC_SECRET])
def process_clip_job(action: str, job_id: str, clip_id: str, url: str = None,
                     series: str = None, guest_name: str = None,
                     reframe_mode: str = "speaker", reframe_offset: float = 0.0):
    import json
    import sys
    import uuid
    sys.path.insert(0, "/root")
    os.chdir("/root")
    _write_cookies()
    import clipper

    def cprog(status, pct, msg=""):
        patch_clip(job_id, clip_id, clip_job={
            "action": action, "status": status, "progress": int(pct), "message": msg})

    try:
        cprog("running", 5, "loading job")
        job = get_job(job_id)
        if not job:
            raise RuntimeError(f"job {job_id} not found")
        outputs = job.get("outputs") or {}
        clips = outputs.get("clips") or []
        target = next((c for c in clips if c.get("clip_id") == clip_id), None)
        if target is None:
            raise RuntimeError(f"clip {clip_id} not in job outputs")
        index = clips.index(target)
        guest_name = guest_name or outputs.get("guest_name")
        series = series or outputs.get("series")
        url = url or job.get("url")
        # The episode_id is a Drive/file id, not a YouTube video id, so a watch
        # URL built from it is dead. Leave blank (placeholder in the description);
        # the app fills the real published YouTube link at upload time.
        episode_url = ""
        episode_title = outputs.get("title") or ""

        # Word timings drive the captions for both actions — load the persisted transcript.
        words_all = []
        if outputs.get("transcript"):
            try:
                tlocal = download_storage(outputs["transcript"], "/tmp/clipjob/transcript.json")
                words_all = json.load(open(tlocal)).get("words", [])
            except Exception as e:
                print(f"transcript fetch failed: {e}")

        tag = f"{action}-{uuid.uuid4().hex[:8]}"     # cache-busting unique filenames

        if action == "reframe":
            cprog("running", 40, "re-cropping")
            spec = {
                "clip_id": clip_id,
                "start": target.get("start"), "end": target.get("end"),
                "hook_line": target.get("hook_line"), "archetype": target.get("archetype"),
                "why": target.get("why", ""), "safety": target.get("safety", "ok"),
                "safety_note": target.get("safety_note", ""),
                "highlight_word": target.get("highlight_word", ""),   # KH-MGX-001 1.2
                # KH-CTP-001: the type survives a reframe unchanged.
                "clip_type": target.get("clip_type") or outputs.get("clip_type") or "best",
                "metadata": target.get("metadata") or {},   # keep captions/banner stable
            }
            rendered = clipper.render_clip(
                spec, url=url, words_all=words_all, series=series, guest_name=guest_name,
                reframe_mode=str(reframe_mode or "speaker"),
                reframe_offset=float(reframe_offset or 0.0), index=index,
                output_root="/tmp/clipjob", with_metadata=False,
                caption_style=outputs.get("caption_style", "classic"),
                usage_ctx={"job_id": job_id, "source": "worker",
                           "episode_ref": outputs.get("episode_id") or job.get("episode_id")})
            cprog("running", 85, "uploading")
            files = upload_clip_files(job_id, clip_id, rendered.get("files"), tag)
            # Swap ONLY this clip's files + framing; keep its copy/metadata. Drop clip_job.
            patch_clip(job_id, clip_id, files=files,
                       framing=rendered.get("framing", "ok"), clip_job=None)

        elif action == "replace":
            cprog("running", 20, "finding a new moment")
            pool = []
            if outputs.get("candidates"):
                try:
                    clocal = download_storage(outputs["candidates"], "/tmp/clipjob/candidates.json")
                    pool = json.load(open(clocal))
                except Exception as e:
                    print(f"candidate fetch failed: {e}")
            # Exclude this clip's range AND every already-emitted clip's range, so the
            # replacement is a genuinely different moment (not the same one re-cropped).
            used = []
            for c in clips:
                try:
                    used.append((float(c["start"]), float(c["end"])))
                except (TypeError, KeyError, ValueError):
                    pass

            def _overlaps(s, e):
                return any(not (e <= us or s >= ue) for us, ue in used)

            pick = next((c for c in pool
                         if c.get("start") is not None and c.get("end") is not None
                         and not _overlaps(float(c["start"]), float(c["end"]))), None)
            if pick is None:
                raise RuntimeError("no fresh unused moment available to replace with")
            cprog("running", 45, "rendering new moment")
            spec = {
                "clip_id": clip_id,                  # the card stays in place
                "start": pick.get("start"), "end": pick.get("end"),
                "hook_line": pick.get("hook_line"), "archetype": pick.get("archetype"),
                "why": "", "safety": pick.get("safety", "ok"),
                "safety_note": pick.get("safety_note", ""), "text": pick.get("text", ""),
                "highlight_word": pick.get("highlight_word", ""),   # KH-MGX-001 1.2
                # Selection data for the manifest entry (score/hook/loopable):
                # pool entries carry the heuristic fit_score; loopable is only
                # ever set by the Grok judgment pass, so a pool pick is False.
                "fit_score": pick.get("fit_score"),
                "loopable": bool(pick.get("loopable", False)),
                # KH-CTP-001: the pool entry was scored under the job's type;
                # carry it so the fresh metadata pack keeps the same lens.
                "clip_type": pick.get("clip_type") or outputs.get("clip_type") or "best",
            }
            rendered = clipper.render_clip(
                spec, url=url, words_all=words_all, series=series, guest_name=guest_name,
                reframe_mode=str(job.get("reframe") or "speaker"), index=index,
                output_root="/tmp/clipjob", with_metadata=True,
                caption_style=outputs.get("caption_style", "classic"),
                episode_title=episode_title, episode_url=episode_url,
                usage_ctx={"job_id": job_id, "source": "worker",
                           "episode_ref": outputs.get("episode_id") or job.get("episode_id")})
            cprog("running", 85, "uploading")
            files = upload_clip_files(job_id, clip_id, rendered.get("files"), tag)
            rendered["files"] = files
            rendered.pop("clip_job", None)
            patch_clip(job_id, clip_id, replace_entry=rendered, clip_job=None)
        else:
            raise RuntimeError(f"unknown action {action!r}")
    except Exception as e:
        patch_clip(job_id, clip_id, clip_job={
            "action": action, "status": "error", "error": str(e)[:500]})
        raise


# ----------------------------------------------------------------------
# Web endpoint: Studio calls this to start a job (protected by a shared token).
# Returns immediately (202); the job runs async via .spawn().
# (Older Modal: rename `fastapi_endpoint` -> `web_endpoint`.)
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# Schedule for YouTube — upload a Drive master to the channel, private +
# publishAt (never a direct live publish). Mirrors process_job's Drive
# fetch + patch pattern; streams progress into youtube_uploads (db/167,168).
# The video is inserted private with publishAt; it goes public at go-live
# once the project's YouTube API compliance audit is approved.
# ----------------------------------------------------------------------
@app.function(image=image, timeout=10800, secrets=[SECRET])
def process_youtube_upload(payload: dict):
    import json as _json
    import re
    import requests

    upload_id = payload["upload_id"]
    master_url = payload.get("master_url") or ""
    snippet = payload.get("snippet") or {}
    status = dict(payload.get("status") or {})
    notify = bool(payload.get("notifySubscribers", True))
    playlist_id = payload.get("playlist_id")
    thumbnail_url = payload.get("thumbnail_url")
    captions_srt = payload.get("captions_srt")
    recording_date = payload.get("recordingDate")

    def prog(stage, pct, msg=""):
        patch_upload(upload_id, {"state": "uploading", "stage": stage,
                                 "progress": int(pct), "message": msg})

    src = None
    try:
        patch_upload(upload_id, {"state": "uploading", "stage": "queued",
                                 "progress": 0, "error": None})

        # 1) Fetch the master. Two sources (kh-studio db/300, 2026-09-03):
        #    - a Google Drive link (the original path, gdown), or
        #    - a file the producer uploaded from their own computer into the
        #      private studio-video bucket, which kh-studio signs and sends as
        #      a plain HTTPS URL. Streamed to disk in chunks, never held in
        #      memory, because a master is often several GB.
        os.makedirs("/tmp/yt", exist_ok=True)
        kind = master_source_kind(master_url)
        if kind == "drive":
            file_id = _drive_file_id(master_url)
            prog("download", 5, "downloading master from Google Drive")
            import gdown
            src = f"/tmp/yt/{file_id}.mp4"
            gdown.download(id=file_id, output=src, quiet=True)
            if not os.path.exists(src) or os.path.getsize(src) < 10000:
                raise RuntimeError("Drive download failed — is the file shared 'anyone with the link'?")
        elif kind == "https":
            prog("download", 5, "downloading the uploaded master")
            src = f"/tmp/yt/{upload_id}.{_master_extension(payload.get('master_file_name'))}"
            _download_https_stream(master_url, src,
                                   on_progress=lambda pct: prog("download", 5 + int(pct * 0.05),
                                                                f"downloading the uploaded master ({int(pct)}%)"))
            if not os.path.exists(src) or os.path.getsize(src) < 10000:
                raise RuntimeError("The uploaded master could not be fetched from storage (empty download).")
        else:
            raise RuntimeError("master_url is neither a Google Drive file link nor an https URL.")

        # 2) Authorise (worker mints its own token from the stored refresh token).
        prog("auth", 10, "authorising with YouTube")
        token = _youtube_token()

        # 3) Resumable videos.insert — private + publishAt.
        prog("upload", 15, "uploading to YouTube")
        body = {"snippet": snippet, "status": status}
        parts = ["snippet", "status"]
        if recording_date:
            body["recordingDetails"] = {"recordingDate": f"{recording_date}T00:00:00Z"}
            parts.append("recordingDetails")
        init = requests.post(
            "https://www.googleapis.com/upload/youtube/v3/videos",
            params={"uploadType": "resumable", "part": ",".join(parts),
                    "notifySubscribers": "true" if notify else "false"},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json; charset=UTF-8",
                     "X-Upload-Content-Type": "video/*"},
            data=_json.dumps(body), timeout=120,
        )
        if init.status_code >= 300:
            raise RuntimeError(f"videos.insert init failed {init.status_code}: {init.text[:300]}")
        session = init.headers.get("Location")
        if not session:
            raise RuntimeError("No resumable session URI returned by YouTube.")
        patch_upload(upload_id, {"resume_uri": session})
        size = os.path.getsize(src)
        # Single streaming PUT (requests streams the file object, never loads it into
        # memory). The session URI is persisted first, so a future hardening can add
        # chunked resume on a dropped connection.
        # Progress (Tony, 2026-09-04): the transfer used to sit at 15% for the whole
        # PUT, so a multi-GB master looked stuck for the entire upload. The reader
        # below counts bytes as http.client pulls them and patches the row at every
        # 5% step, mapped onto the 15..80 band this stage owns.
        def _on_sent(done, total):
            pct = int(done * 100 / total) if total else 0
            prog("upload", 15 + int(pct * 0.65), f"uploading to YouTube ({pct}%)")

        with open(src, "rb") as f:
            up = requests.put(
                session,
                headers={"Authorization": f"Bearer {token}",
                         "Content-Length": str(size), "Content-Type": "video/*"},
                data=_ProgressReader(f, size, _on_sent), timeout=None,
            )
        if up.status_code >= 300:
            raise RuntimeError(f"upload failed {up.status_code}: {up.text[:300]}")
        video_id = up.json().get("id")
        if not video_id:
            raise RuntimeError("Upload succeeded but YouTube returned no video id.")
        patch_upload(upload_id, {"state": "processing", "stage": "processing", "progress": 80,
                                 "video_id": video_id, "quota_units_used": 1600,
                                 "message": "video uploaded, running side calls"})

        # 4) Best-effort side calls: thumbnail, captions, playlist.
        upl = "https://www.googleapis.com/upload/youtube/v3"
        yt = "https://www.googleapis.com/youtube/v3"
        if thumbnail_url:
            try:
                img = requests.get(thumbnail_url, timeout=120)
                if img.ok:
                    ct = img.headers.get("Content-Type", "image/png")
                    tset = requests.post(
                        f"{upl}/thumbnails/set", params={"videoId": video_id},
                        headers={"Authorization": f"Bearer {token}", "Content-Type": ct},
                        data=img.content, timeout=120)
                    if tset.ok:
                        patch_upload(upload_id, {"thumbnail_pushed": True})
                    else:
                        print(f"thumbnails.set {tset.status_code}: {tset.text[:200]}")
            except Exception as e:
                print(f"thumbnail set failed: {e}")
        if captions_srt:
            try:
                meta = {"snippet": {"videoId": video_id,
                                    "language": snippet.get("defaultAudioLanguage") or "en",
                                    "name": "English", "isDraft": False}}
                cap = requests.post(
                    f"{upl}/captions", params={"part": "snippet"},
                    headers={"Authorization": f"Bearer {token}"},
                    files={"metadata": ("meta.json", _json.dumps(meta), "application/json"),
                           "file": ("captions.srt", captions_srt.encode("utf-8"), "application/octet-stream")},
                    timeout=120)
                if cap.ok:
                    patch_upload(upload_id, {"captions_pushed": True})
                else:
                    print(f"captions.insert {cap.status_code}: {cap.text[:200]}")
            except Exception as e:
                print(f"captions insert failed: {e}")
        if playlist_id:
            try:
                pl = requests.post(
                    f"{yt}/playlistItems", params={"part": "snippet"},
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    data=_json.dumps({"snippet": {"playlistId": playlist_id,
                                                  "resourceId": {"kind": "youtube#video", "videoId": video_id}}}),
                    timeout=60)
                if pl.ok:
                    patch_upload(upload_id, {"playlist_pushed": True})
                else:
                    print(f"playlistItems.insert {pl.status_code}: {pl.text[:200]}")
            except Exception as e:
                print(f"playlist add failed: {e}")

        # 5) Done: uploaded private + scheduled. Goes public at publishAt once audited.
        patch_upload(upload_id, {"state": "scheduled", "stage": "scheduled", "progress": 100,
                                 "message": "Uploaded private and scheduled for go-live."})
    except Exception as e:
        patch_upload(upload_id, {"state": "failed", "error": str(e)[:500]})
        print(f"process_youtube_upload failed: {e}")
    finally:
        try:
            if src and os.path.exists(src):
                os.remove(src)
        except Exception:
            pass


@app.function(image=image, secrets=[SECRET])
@modal.fastapi_endpoint(method="POST")
def generate(payload: dict, authorization: str = fastapi.Header(default="")):
    if authorization != f"Bearer {os.environ['WORKER_TOKEN']}":
        raise fastapi.HTTPException(status_code=401, detail="unauthorized")

    # Schedule for YouTube — upload a Drive master to the channel (private + publishAt).
    _action = (payload.get("action") or "").strip().lower()
    if _action == "upload_youtube":
        for field in ("upload_id", "master_url"):
            if not payload.get(field):
                raise fastapi.HTTPException(status_code=400, detail=f"missing {field}")
        process_youtube_upload.spawn(payload)
        return {"accepted": True, "upload_id": payload["upload_id"], "action": _action}

    # Landscape audiogram for a window of an episode (KH-VRL-001, action="video").
    if _action == "video":
        err = validate_video_payload(payload)
        if err:
            raise fastapi.HTTPException(status_code=400, detail=err)
        process_video_job.spawn(payload)
        return {"accepted": True, "job_id": payload["job_id"], "action": _action}

    # Standalone longer-form audiogram, picked by clip_type + duration (KH-AUD-001,
    # action="audiogram"). clip_type validated here (needs TYPE_PROFILES), the rest
    # in validate_audiogram_payload — same split action="generate" already uses below.
    if _action == "audiogram":
        err = validate_audiogram_payload(payload)
        if err:
            raise fastapi.HTTPException(status_code=400, detail=err)
        clip_type = str(payload.get("clip_type") or "best")
        import sys as _sys3
        _sys3.path.insert(0, "/root")
        from src import detect as _detect_mod3
        if clip_type not in _detect_mod3.TYPE_PROFILES:
            raise fastapi.HTTPException(status_code=400, detail=f"unknown clip_type {clip_type!r}")
        process_audiogram_job.spawn(payload)
        return {"accepted": True, "job_id": payload["job_id"], "action": _action}

    # Mechanical QC on a finished episode master (KH-QC-001, action="episode_qc").
    # Placed BEFORE the fall-through unknown-action 400 below. Check names are
    # validated here (they need src/qc.py's CHECK_TYPES), the rest in
    # validate_episode_qc_payload, the same split the other actions use.
    if _action == "episode_qc":
        err = validate_episode_qc_payload(payload)
        if err:
            raise fastapi.HTTPException(status_code=400, detail=err)
        requested = payload.get("checks") or []
        if requested:
            import sys as _sys4
            _sys4.path.insert(0, "/root")
            from src import qc as _qc_mod
            unknown = [c for c in requested if c not in _qc_mod.CHECK_TYPES]
            if unknown:
                raise fastapi.HTTPException(status_code=400,
                                            detail=f"unknown check {unknown[0]!r}")
        process_episode_qc_job.spawn(payload)
        return {"accepted": True, "job_id": payload["job_id"], "action": _action}

    # Per-clip ops share this endpoint, distinguished by `action`. An absent `action`
    # is a normal full-generate job (unchanged contract).
    action = (payload.get("action") or "").strip().lower()
    if action in ("reframe", "replace"):
        for field in ("job_id", "clip_id"):
            if not payload.get(field):
                raise fastapi.HTTPException(status_code=400, detail=f"missing {field}")
        process_clip_job.spawn(
            action, payload["job_id"], payload["clip_id"], payload.get("url"),
            payload.get("series"), payload.get("guest_name"),
            str(payload.get("reframe") or "speaker"),
            float(payload.get("reframe_offset") or 0.0),
        )
        return {"accepted": True, "job_id": payload["job_id"],
                "clip_id": payload["clip_id"], "action": action}
    if action and action != "generate":
        raise fastapi.HTTPException(status_code=400, detail=f"unknown action {action}")

    for field in ("job_id", "url"):
        if not payload.get(field):
            raise fastapi.HTTPException(status_code=400, detail=f"missing {field}")

    # Exact-cut (Wave 1): an optional list of board-approved windows to render
    # instead of auto-selecting. Validated here so a bad payload fails fast (400)
    # rather than mid-render. Absent -> the unchanged auto-select flow.
    moments = payload.get("moments")
    if moments is not None:
        import sys as _sys
        _sys.path.insert(0, "/root")
        from src import moments as _moments_mod
        err = _moments_mod.validate_moments(moments)
        if err:
            raise fastapi.HTTPException(status_code=400, detail=f"invalid moments: {err}")

    # KH-CTP-001: the clip type lens (default "best" = current behaviour).
    # Validated against TYPE_PROFILES (the single source of truth) so a typo
    # fails fast instead of silently degrading mid-render. reviewer_anchors is
    # an optional list of Episode Reviewer evidence quotes (advisory only).
    clip_type = str(payload.get("clip_type") or "best")
    import sys as _sys2
    _sys2.path.insert(0, "/root")
    from src import detect as _detect_mod
    if clip_type not in _detect_mod.TYPE_PROFILES:
        raise fastapi.HTTPException(status_code=400, detail=f"unknown clip_type {clip_type!r}")
    reviewer_anchors = payload.get("reviewer_anchors")
    if reviewer_anchors is not None and not isinstance(reviewer_anchors, list):
        raise fastapi.HTTPException(status_code=400, detail="reviewer_anchors must be a list")
    # hook_phrases: proven phrases mined from KH's own winning Shorts, fed to the
    # metadata prompt as phrase directions (remix the psychology, never copy).
    hook_phrases = payload.get("hook_phrases")
    if hook_phrases is not None:
        if not isinstance(hook_phrases, list) or not all(isinstance(p, str) for p in hook_phrases):
            raise fastapi.HTTPException(status_code=400, detail="hook_phrases must be a list of strings")

    # KH-CTP-001 Phase 2: an optional SPREAD across multiple type lenses in one
    # job, instead of `count` clips of one repeated `clip_type`. A list of 2+
    # distinct type keys, each validated against TYPE_PROFILES the same way
    # the single clip_type is above (a typo fails fast, not mid-render).
    # Absent/empty (default) -> the unchanged single-type path, `clip_type`.
    clip_types = payload.get("clip_types")
    if clip_types is not None:
        if not isinstance(clip_types, list) or len(clip_types) < 2:
            raise fastapi.HTTPException(
                status_code=400, detail="clip_types must be a list of 2 or more type keys")
        seen, norm = set(), []
        for t in clip_types:
            if not isinstance(t, str) or t not in _detect_mod.TYPE_PROFILES:
                raise fastapi.HTTPException(status_code=400, detail=f"unknown clip_types entry {t!r}")
            if t not in seen:
                seen.add(t)
                norm.append(t)
        clip_types = norm

    process_job.spawn(
        payload["job_id"], payload["url"], payload.get("series"),
        int(payload.get("count", 5)), bool(payload.get("audiogram", True)),
        # Honour the Shorts Engine's reframe request ("speaker" = follow the speaker).
        # Default to speaker-follow so older callers keep tracked framing.
        str(payload.get("reframe") or "speaker"),
        # The real guest's name (or None) -> threaded into clip copy.
        payload.get("guest_name"),
        # Plan B: an optional pre-made transcript (kh-studio's AssemblyAI words)
        # so the worker can skip re-transcribing. Absent -> we transcribe the
        # source ourselves, exactly as before.
        payload.get("transcript"),
        # Exact-cut windows (or None for auto-select).
        moments,
        # KH-MGX-001 Wave 2: "classic" (libass, default) | "kinetic" (Remotion).
        # Not in the contract yet (kh-studio doesn't send this field today) —
        # defaults to classic so every existing caller is unaffected.
        str(payload.get("caption_style") or "classic"),
        # KH-CTP-001: the selection lens ("best" = unchanged behaviour) + the
        # optional advisory Episode Reviewer anchors for typed jobs.
        clip_type,
        reviewer_anchors,
        # Data-backed hook-phrase directions for the metadata prompt (or None).
        hook_phrases,
        # KH-CTP-001 Phase 2: the spread type list (or None = single-type path).
        clip_types,
    )
    return {"accepted": True, "job_id": payload["job_id"]}
