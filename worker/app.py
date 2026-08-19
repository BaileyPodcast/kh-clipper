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
            "caption_style": result.get("caption_style", "classic")}


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
                reviewer_anchors: list = None, hook_phrases: list = None):
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
                hook_phrases=hook_phrases,
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
                hook_phrases=hook_phrases,
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
        patch_job(job_id, {"status": "done", "progress": 100, "stage": "done",
                           "episode_id": result.get("episode_id"), "outputs": outputs,
                           "message": f"{len(outputs['clips'])} clips ready"})
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

        # 1) Fetch the Drive master (same gdown path as the Shorts worker).
        drive = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?id=|.*[?&]id=)([A-Za-z0-9_-]{20,})", master_url)
        if not drive:
            raise RuntimeError("master_url is not a Google Drive file link.")
        file_id = drive.group(1)
        prog("download", 5, "downloading master from Google Drive")
        import gdown
        os.makedirs("/tmp/yt", exist_ok=True)
        src = f"/tmp/yt/{file_id}.mp4"
        gdown.download(id=file_id, output=src, quiet=True)
        if not os.path.exists(src) or os.path.getsize(src) < 10000:
            raise RuntimeError("Drive download failed — is the file shared 'anyone with the link'?")

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
        with open(src, "rb") as f:
            up = requests.put(
                session,
                headers={"Authorization": f"Bearer {token}",
                         "Content-Length": str(size), "Content-Type": "video/*"},
                data=f, timeout=None,
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
    )
    return {"accepted": True, "job_id": payload["job_id"]}
