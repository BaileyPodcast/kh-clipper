"""
KH Clipper — Stage 3: cut.

Turns the approved moments in <id>.clips.json into real video files.

Audio-first architecture (from the handoff): the full video never sits on disk.
By default we pull ONLY the chosen 1080p sections straight from YouTube with
yt-dlp, then ffmpeg trims each to a frame-accurate clip. If you already have the
source on disk, pass --source FILE and it cuts locally with no download.

Trauma-informed: clips Grok rated "exclude" were already dropped in detect, so
they never reach here. Clips rated "review" ARE cut (a producer has to watch
them to approve), but they are tagged in the manifest. Use --safe-only to skip
review clips entirely.

Input : output/<id>.clips.json   (+ the source: YouTube id, or --source FILE)
Output: output/clips/<clip_id>.mp4   and   output/<id>.cuts.json (manifest)

Run:
    python -m src.cut output/<id>.clips.json
    python -m src.cut output/<id>.clips.json --source input/<id>.mp4
    python -m src.cut output/<id>.clips.json --safe-only
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess

MAX_HEIGHT = 1080      # Shorts play on phones; 4K just wastes disk + time.
MAX_CLIP_SEC = 35.0    # HARD CEILING for social/Shorts/Reels. Nothing exports longer.


def _ff(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-1200:])


def cut_local(source: str, start: float, end: float, out_path: str):
    """Frame-accurate trim from a local source file (re-encode for exact cuts)."""
    _ff([
        "ffmpeg", "-y", "-i", source, "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-vf", f"scale=-2:'min({MAX_HEIGHT},ih)'",
        "-c:a", "aac", "-movflags", "+faststart", out_path,
    ])


def cut_remote(url: str, start: float, end: float, out_path: str):
    """Pull ONLY this 1080p section from YouTube, frame-accurate at the cuts."""
    import yt_dlp
    try:
        from . import ytdlp
    except ImportError:
        import ytdlp
    opts = {
        "format": f"bestvideo[height<={MAX_HEIGHT}]+bestaudio/best[height<={MAX_HEIGHT}]/best",
        "outtmpl": out_path.replace(".mp4", ".%(ext)s"),
        "download_ranges": yt_dlp.utils.download_range_func(None, [(start, end)]),
        "force_keyframes_at_cuts": True,   # exact in/out points
        "merge_output_format": "mp4",
        "quiet": True, "no_warnings": True,
        **ytdlp.resilience_opts(),         # player-client + cookies bypass for cloud IPs
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


def run(clips_json: str, source: str | None = None, safe_only: bool = False,
        max_sec: float = MAX_CLIP_SEC):
    data = json.load(open(clips_json))
    vid = data.get("source") or data.get("video_id") or "clip"
    clips = data.get("clips", [])
    out_dir = os.path.join(os.path.dirname(clips_json) or ".", "clips")
    os.makedirs(out_dir, exist_ok=True)

    url = None
    if not source:
        sid = clips[0].get("source_video_id") if clips else data.get("source")
        if not sid:
            # RuntimeError, not SystemExit: SystemExit is a BaseException, so the
            # worker's `except Exception` never caught it — the job died silently
            # and hung at the cut stage until the stall watchdog expired it.
            raise RuntimeError("No --source file and no source_video_id in clips.json")
        url = f"https://www.youtube.com/watch?v={sid}"

    cuts, skipped = [], []
    for c in clips:
        safety = c.get("safety", "ok")
        if safety == "exclude":
            continue                       # belt-and-braces; should already be gone
        if safe_only and safety == "review":
            skipped.append(c.get("clip_id"))
            continue
        cid = c.get("clip_id", f"{vid}-{len(cuts)+1:02d}")
        start, end = float(c["start"]), float(c["end"])
        # HARD CEILING: never export longer than max_sec. Trim the END (keeps the
        # hook/opening, which is what earns the watch) and flag it.
        trimmed = False
        if end - start > max_sec:
            end = start + max_sec
            trimmed = True
            print(f"  ~ {cid} over {max_sec:.0f}s — trimmed end to keep it under the cap")
        out_path = os.path.join(out_dir, f"{cid}.mp4")
        try:
            if source:
                cut_local(source, start, end, out_path)
            else:
                cut_remote(url, start, end, out_path)
        except Exception as e:
            print(f"  ! {cid} failed: {str(e)[:160]}")
            continue
        cuts.append({
            "clip_id": cid,
            "file": os.path.relpath(out_path, os.path.dirname(clips_json) or "."),
            "start": round(start, 2), "end": round(end, 2),
            "duration": round(end - start, 2),
            "trimmed_to_cap": trimmed,
            "aspect": "16:9",                       # Stage 4 reframe makes it 9:16
            "hook_line": c.get("hook_line", ""),
            "highlight_word": c.get("highlight_word", ""),
            "lead_with": c.get("lead_with", ""),
            "safety": safety,
            "safety_note": c.get("safety_note", ""),
        })
        flag = f"  [{safety.upper()}]" if safety != "ok" else ""
        print(f"  cut {cid}  {start:.1f}-{end:.1f} ({end-start:.0f}s){flag}")

    manifest = clips_json.replace(".clips.json", ".cuts.json")
    json.dump({"video_id": data.get("video_id"),
               "source": source or url,
               "cuts": cuts}, open(manifest, "w"), indent=2, ensure_ascii=False)
    print(f"\nCut {len(cuts)} clips -> {out_dir}")
    if skipped:
        print(f"Skipped {len(skipped)} review clips (--safe-only): {', '.join(skipped)}")
    print(f"Manifest -> {manifest}")
    print("Next: Stage 4 reframe (16:9 -> 9:16), then Stage 5 caption.")
    return manifest


def main():
    ap = argparse.ArgumentParser(description="Stage 3: cut approved moments into clips")
    ap.add_argument("clips_json", help="output/<id>.clips.json")
    ap.add_argument("--source", default=None, help="local source video (skip download)")
    ap.add_argument("--safe-only", action="store_true", help="skip clips flagged 'review'")
    ap.add_argument("--max-sec", type=float, default=MAX_CLIP_SEC,
                    help=f"hard max clip length in seconds (default {MAX_CLIP_SEC:.0f})")
    args = ap.parse_args()
    run(args.clips_json, source=args.source, safe_only=args.safe_only, max_sec=args.max_sec)


if __name__ == "__main__":
    main()
