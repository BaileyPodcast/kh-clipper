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
    # pillow + numpy power the branded audiogram renderer (src/audiogram.py): it draws
    # each frame of the KH design-suite audiogram and ffmpeg muxes the clip audio under it.
    .pip_install("yt-dlp", "requests", "mediapipe", "ffmpeg-python", "fastapi[standard]",
                 "gdown", "pillow", "numpy")
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
            "series": result.get("series"), "guest_name": result.get("guest_name"),
            "clips": clips, "review": review}


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


@app.function(image=image, timeout=1800, secrets=[SECRET, COOKIE_SECRET, XAI_SECRET])
def process_job(job_id: str, url: str, series: str = None,
                count: int = 5, audiogram: bool = True, reframe: str = "speaker",
                guest_name: str = None):
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
            result = clipper.run(
                source_file=src, episode_id=file_id, series=series, count=count,
                make_audiogram=audiogram, progress_cb=progress, output_root="/tmp/job",
                reframe_mode=reframe, guest_name=guest_name,
            )
        else:
            result = clipper.run(
                url=url, series=series, count=count, make_audiogram=audiogram,
                progress_cb=progress, output_root="/tmp/job", reframe_mode=reframe,
                guest_name=guest_name,
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
    except Exception as e:
        patch_job(job_id, {"status": "error", "error": str(e)[:500]})
        raise


# ----------------------------------------------------------------------
# Per-clip op: the kh-studio "reframe" / "replace" buttons. Re-render ONE clip of a
# finished job, driving progress through outputs.clips[i].clip_job and NEVER touching
# the row `status` (it stays 'done' so the results view doesn't collapse).
# ----------------------------------------------------------------------
@app.function(image=image, timeout=900, secrets=[SECRET, COOKIE_SECRET, XAI_SECRET])
def process_clip_job(action: str, job_id: str, clip_id: str, url: str = None,
                     series: str = None, guest_name: str = None,
                     reframe_mode: str = "speaker"):
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
        episode_url = f"https://www.youtube.com/watch?v={outputs.get('episode_id') or job.get('episode_id') or ''}"
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
                "metadata": target.get("metadata") or {},   # keep captions/banner stable
            }
            rendered = clipper.render_clip(
                spec, url=url, words_all=words_all, series=series, guest_name=guest_name,
                reframe_mode=str(reframe_mode or "speaker"), index=index,
                output_root="/tmp/clipjob", with_metadata=False)
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
            }
            rendered = clipper.render_clip(
                spec, url=url, words_all=words_all, series=series, guest_name=guest_name,
                reframe_mode=str(job.get("reframe") or "speaker"), index=index,
                output_root="/tmp/clipjob", with_metadata=True,
                episode_title=episode_title, episode_url=episode_url)
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
@app.function(image=image, secrets=[SECRET])
@modal.fastapi_endpoint(method="POST")
def generate(payload: dict, authorization: str = fastapi.Header(default="")):
    if authorization != f"Bearer {os.environ['WORKER_TOKEN']}":
        raise fastapi.HTTPException(status_code=401, detail="unauthorized")

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
        )
        return {"accepted": True, "job_id": payload["job_id"],
                "clip_id": payload["clip_id"], "action": action}
    if action and action != "generate":
        raise fastapi.HTTPException(status_code=400, detail=f"unknown action {action}")

    for field in ("job_id", "url"):
        if not payload.get(field):
            raise fastapi.HTTPException(status_code=400, detail=f"missing {field}")
    process_job.spawn(
        payload["job_id"], payload["url"], payload.get("series"),
        int(payload.get("count", 5)), bool(payload.get("audiogram", True)),
        # Honour the Shorts Engine's reframe request ("speaker" = follow the speaker).
        # Default to speaker-follow so older callers keep tracked framing.
        str(payload.get("reframe") or "speaker"),
        # The real guest's name (or None) -> threaded into clip copy.
        payload.get("guest_name"),
    )
    return {"accepted": True, "job_id": payload["job_id"]}
