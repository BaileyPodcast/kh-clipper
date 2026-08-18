"""
KH Clipper loudness normalisation to YouTube's playback target.

YouTube normalises playback to about -14 LUFS. A Short delivered louder gets
turned down (harmless) but one delivered quieter is NOT turned up, so it plays
soft against every neighbouring clip in the feed. This post-step runs loudnorm
(I=-14, TP=-1.0, LRA=11) on a FINISHED mp4, re-encoding audio only (the video
stream is stream-copied, so the pinned CRF 18 video encode is untouched), then
atomically replaces the file in place.

Non-fatal by design: any failure logs and keeps the unnormalised file, because
a slightly quiet clip is always better than no clip. Call it on every final
deliverable (classic caption exports, kinetic exports, audiograms).
"""
from __future__ import annotations
import os
import subprocess

TARGET_I = "-14"      # integrated loudness (YouTube's playback target, LUFS)
TARGET_TP = "-1.0"    # true peak ceiling (dBTP)
TARGET_LRA = "11"     # loudness range

AUDIO_BITRATE = "192k"


def build_command(in_path: str, out_path: str) -> list[str]:
    """The exact ffmpeg command (pure, unit-testable without running ffmpeg)."""
    return [
        "ffmpeg", "-y", "-i", in_path,
        "-c:v", "copy",
        "-af", f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}",
        "-c:a", "aac", "-b:a", AUDIO_BITRATE,
        "-movflags", "+faststart",
        out_path,
    ]


def normalize(path: str) -> str:
    """Loudness-normalise a finished mp4 IN PLACE (atomic replace). Returns the
    same path. Never raises: on any failure the original file is kept and a
    short note is printed, so a loudness hiccup can never cost the clip."""
    tmp = f"{path}.loudnorm.tmp.mp4"
    try:
        r = subprocess.run(build_command(path, tmp), capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "")[-300:] or "ffmpeg failed")
        if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
            raise RuntimeError("loudnorm wrote no output")
        os.replace(tmp, path)
    except Exception as e:
        print(f"      ~ loudness normalise skipped ({str(e)[:160]})")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
    return path
