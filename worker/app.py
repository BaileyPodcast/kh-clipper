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
        WORKER_TOKEN=<a-long-random-shared-token>
    modal deploy worker/app.py
    # -> prints the web endpoint URL. Put it + WORKER_TOKEN in Studio's server env.

Storage: a PRIVATE bucket named `shorts`. Studio reads via short-lived signed URLs.
"""
import os

import fastapi          # provided by Modal's client for web endpoints
import modal

APP_NAME = "kh-shorts-worker"
BUCKET = "shorts"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Image: Python 3.12 (broad compatibility) + ffmpeg + the pipeline deps + the repo.
# Local code/assets are copied in as the final layers. `output/` and `venv/` are
# excluded — they are not needed in the image.
image = (
    modal.Image.debian_slim(python_version="3.12")
    # ffmpeg for cut/reframe/caption; the GL libs are MediaPipe's runtime deps — without
    # them BlazeFace fails to init (libGLESv2.so.2 missing) and every clip silently
    # centre-crops instead of following the speaker (see src/reframe.py fail-soft path).
    .apt_install("ffmpeg", "libgl1", "libglib2.0-0", "libegl1", "libgles2")
    .pip_install("yt-dlp", "requests", "mediapipe", "ffmpeg-python", "fastapi[standard]", "gdown")
    .add_local_file(os.path.join(REPO_ROOT, "clipper.py"), "/root/clipper.py")
    .add_local_dir(os.path.join(REPO_ROOT, "src"), "/root/src")
    .add_local_dir(os.path.join(REPO_ROOT, "assets"), "/root/assets")
)

app = modal.App(APP_NAME)
SECRET = modal.Secret.from_name("kh-shorts")
# Cookies live in their own secret so refreshing them is one trivial command and
# never touches the API keys. (Create the `yt-cookies` secret before deploying.)
COOKIE_SECRET = modal.Secret.from_name("yt-cookies")
# xAI key in its own secret too — single-value, easy to set/rotate. Listed LAST so its
# XAI_API_KEY overrides any stale value in kh-shorts.
XAI_SECRET = modal.Secret.from_name("xai")

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


def upload_file(local_path, remote_path):
    """Upload one file to the private Storage bucket (upsert). Returns the storage path."""
    import requests
    ext = os.path.splitext(local_path)[1].lower()
    with open(local_path, "rb") as f:
        r = requests.post(
            f"{_sb_url()}/storage/v1/object/{BUCKET}/{remote_path}",
            headers=_sb_headers({"Content-Type": CONTENT_TYPES.get(ext, "application/octet-stream"),
                                 "x-upsert": "true"}),
            data=f.read(), timeout=300,
        )
    r.raise_for_status()
    return f"{BUCKET}/{remote_path}"


# Human-readable file labels so downloads are obvious (not cryptic clip ids).
KIND_LABEL = {
    "shorts": "Shorts Clip",
    "universal": "Reel-TikTok Clip",
    "audiogram_square": "Audiogram Square Clip",
    "audiogram_vertical": "Audiogram Vertical Clip",
}


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
            "series": result.get("series"), "clips": clips, "review": review}


# ----------------------------------------------------------------------
# The job: run the pipeline, stream progress, upload outputs.
# ----------------------------------------------------------------------
@app.function(image=image, timeout=1800, secrets=[SECRET, COOKIE_SECRET, XAI_SECRET])
def process_job(job_id: str, url: str, series: str = None,
                count: int = 5, audiogram: bool = True):
    import sys
    sys.path.insert(0, "/root")
    os.chdir("/root")
    # YouTube blocks datacenter IPs; if a YT_COOKIES secret is present, write it to a
    # file and point yt-dlp at it (src/ytdlp.py reads YT_COOKIES_FILE).
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
            result = clipper.run(
                source_file=src, episode_id=file_id, series=series, count=count,
                make_audiogram=audiogram, progress_cb=progress, output_root="/tmp/job",
            )
        else:
            result = clipper.run(
                url=url, series=series, count=count, make_audiogram=audiogram,
                progress_cb=progress, output_root="/tmp/job",
            )
        patch_job(job_id, {"stage": "uploading", "progress": 96, "message": "uploading outputs"})
        outputs = upload_outputs(job_id, result)
        patch_job(job_id, {"status": "done", "progress": 100, "stage": "done",
                           "episode_id": result.get("episode_id"), "outputs": outputs,
                           "message": f"{len(outputs['clips'])} clips ready"})
    except Exception as e:
        patch_job(job_id, {"status": "error", "error": str(e)[:500]})
        raise


# ----------------------------------------------------------------------
# Web endpoint: Studio calls this to start a job (protected by a shared token).
# Returns immediately (202); the job runs async via .spawn().
# (Older Modal: rename `fastapi_endpoint` -> `web_endpoint`.)
# ----------------------------------------------------------------------
@app.function(image=image, secrets=[SECRET])
@modal.fastapi_endpoint(method="POST")
def generate(payload: dict, authorization: str = fastapi.Header(default="")):
    if authorization != f"Bearer {os.environ['WORKER_TOKEN']}":
        raise fastapi.HTTPException(status_code=401, detail="unauthorized")
    for field in ("job_id", "url"):
        if not payload.get(field):
            raise fastapi.HTTPException(status_code=400, detail=f"missing {field}")
    process_job.spawn(
        payload["job_id"], payload["url"], payload.get("series"),
        int(payload.get("count", 5)), bool(payload.get("audiogram", True)),
    )
    return {"accepted": True, "job_id": payload["job_id"]}
