"""
Stage 0: fetch — the front door.

Takes a YouTube URL and pulls the AUDIO only (tiny — ~50-100MB/hr),
which is all the transcribe stage needs. The full video never lands
on disk here; video sections get pulled later, only for the clips
we actually keep.

Needs ffmpeg installed on the system (Mac: brew install ffmpeg).
"""


def fetch_local(source_path, episode_id=None, out_dir="input"):
    """Prep a LOCAL master video (e.g. downloaded from Google Drive) for the pipeline:
    extract 16kHz mono wav for transcription. Returns the same metadata shape as
    fetch_audio so the rest of the pipeline is unchanged."""
    import json
    import os
    import subprocess
    os.makedirs(out_dir, exist_ok=True)
    sid = episode_id or os.path.splitext(os.path.basename(source_path))[0]
    audio = f"{out_dir}/{sid}.wav"
    subprocess.run(["ffmpeg", "-y", "-i", source_path, "-ar", "16000", "-ac", "1", audio],
                   check=True, capture_output=True)
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", source_path], capture_output=True, text=True)
    try:
        dur = int(float(json.loads(r.stdout)["format"]["duration"]))
    except Exception:
        dur = 0
    return {"id": sid, "title": sid, "duration_sec": dur, "audio_path": audio}


def fetch_audio(url, out_dir="input"):
    """
    Download audio-only from a YouTube URL, as 16kHz mono wav
    (exactly what Whisper wants). Returns a dict of metadata:
        { id, title, duration_sec, audio_path }
    """
    import yt_dlp   # lazy: local/offline runs don't need yt-dlp installed
    try:
        from . import ytdlp
    except ImportError:
        import ytdlp
    opts = {
        "format": "bestaudio/best",
        "outtmpl": f"{out_dir}/%(id)s.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
            }
        ],
        # Force 16kHz mono — smallest useful file for transcription
        "postprocessor_args": ["-ar", "16000", "-ac", "1"],
        **ytdlp.resilience_opts(),     # player-client + cookies bypass for cloud IPs
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    return {
        "id": info["id"],
        "title": info.get("title", "untitled"),
        "duration_sec": info.get("duration", 0),
        "audio_path": f"{out_dir}/{info['id']}.wav",
    }
