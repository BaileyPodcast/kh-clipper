"""
KH Clipper — Stage 5c: branded end screen.

Every finished Short ends on a KH-branded end screen (Subscribe/Like/Share or the
platform pills). The short dissolves into the end screen with a short transition, and
the guest's audio fades out into the (silent) outro.

Two options live in assets/endscreens/ (01_*, 02_*). Across a run we ALTERNATE them
per clip: clip 1 -> option 1, clip 2 -> option 2, clip 3 -> option 1, ...

    python -m src.endscreen <clip.mp4> <out.mp4> --index 0
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENDSCREEN_DIR = os.path.join(_HERE, "assets", "endscreens")
TRANSITION_SEC = 0.5
FPS = 30


def endscreens():
    """The end-screen options, sorted (01_*, 02_*) so alternation order is stable."""
    if not os.path.isdir(ENDSCREEN_DIR):
        return []
    return sorted(
        os.path.join(ENDSCREEN_DIR, f)
        for f in os.listdir(ENDSCREEN_DIR) if f.lower().endswith(".mp4")
    )


def available():
    return len(endscreens()) > 0


def pick(index):
    """Alternate the options by clip index (0 -> option 1, 1 -> option 2, ...)."""
    es = endscreens()
    return es[index % len(es)] if es else None


def _dur(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", path], capture_output=True, text=True)
    return float(json.loads(r.stdout)["format"]["duration"])


def append(video_in, endscreen_path, out_path, transition=TRANSITION_SEC, fps=FPS):
    """Dissolve `video_in` into `endscreen_path` and write `out_path`.
    The end screens are silent, so the clip audio fades out across the transition."""
    d0 = _dur(video_in)
    de = _dur(endscreen_path)
    off = max(0.1, d0 - transition)             # start the dissolve `transition`s before the end
    filt = (
        f"[0:v]fps={fps},format=yuv420p,setsar=1,settb=AVTB[v0];"
        f"[1:v]fps={fps},format=yuv420p,setsar=1,settb=AVTB[v1];"
        f"[v0][v1]xfade=transition=fade:duration={transition}:offset={off:.3f}[v];"
        # clip audio (real) crossfaded into generated silence -> a clean fade-out.
        f"[0:a]aresample=48000,asetpts=PTS-STARTPTS[a0];"
        f"[2:a]asetpts=PTS-STARTPTS[a2];"
        f"[a0][a2]acrossfade=d={transition}[a]"
    )
    cmd = [
        "ffmpeg", "-y", "-i", video_in, "-i", endscreen_path,
        "-f", "lavfi", "-t", f"{de:.2f}", "-i", "anullsrc=r=48000:cl=stereo",
        "-filter_complex", filt, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-movflags", "+faststart", out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-1400:])
    return out_path


def append_to(video_path, index, transition=TRANSITION_SEC):
    """Append the alternating end screen onto a finished clip IN PLACE. No-op (returns
    the option name or None) if no end screens are installed."""
    es = pick(index)
    if not es:
        return None
    tmp = video_path + ".es.mp4"
    append(video_path, es, tmp, transition=transition)
    os.replace(tmp, video_path)
    return os.path.splitext(os.path.basename(es))[0]


def main():
    ap = argparse.ArgumentParser(description="Stage 5c: append a branded end screen")
    ap.add_argument("clip")
    ap.add_argument("out")
    ap.add_argument("--index", type=int, default=0, help="clip index (alternates the option)")
    a = ap.parse_args()
    es = pick(a.index)
    if not es:
        print("No end screens in assets/endscreens/"); return
    append(a.clip, es, a.out)
    print(f"end screen ({os.path.basename(es)}) -> {a.out}")


if __name__ == "__main__":
    main()
