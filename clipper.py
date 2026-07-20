"""
KH Clipper — turn a long-form episode into finished, branded vertical clips.

ONE command, end to end:
    python clipper.py "https://youtube.com/watch?v=..."

Resume / offline options:
    python clipper.py --transcript output/<id>.transcript.json        # skip fetch+transcribe
    python clipper.py "URL" --source input/<id>.mp4                    # cut from a local file
    python clipper.py --transcript <t.json> --source <video.mp4>       # fully local, no download
    python clipper.py "URL" --no-llm                                   # heuristic detect only
    python clipper.py "URL" --safe-only --max-sec 30                   # skip review clips, 30s cap

Pipeline (each stage in src/):
    0 fetch      yt-dlp pulls audio-only
    1 transcribe Grok STT (default) / WhisperX fallback
    2 detect     find Kintsugi moments (trauma-informed + safety gate)
    3 cut        pull only the chosen 1080p sections, frame-accurate, <=35s cap
    4 reframe    16:9 -> 9:16
    5 caption    Olive Pill karaoke + gentle CTAs + logo, dual export

Output: output/final/<clip_id>_shorts.mp4  and  _universal.mp4
"""
import argparse
import json
import os

from src import (fetch, transcribe, detect, metadata, cut, reframe, caption,
                 review, audiogram, endscreen, moments as moments_mod)


def _transcribe_local(source_path, provider, episode_id=None, output_root="output", usage_ctx=None):
    print(f"[0/5] Using local master: {source_path}")
    meta = fetch.fetch_local(source_path, episode_id=episode_id)
    print(f"      {meta['title']}  ({meta['duration_sec'] // 60}m {meta['duration_sec'] % 60}s)")
    print(f"[1/5] Transcribing via {provider}")
    try:
        result = transcribe.transcribe(meta["audio_path"], provider=provider, usage_ctx=usage_ctx)
    except Exception as e:
        try:
            import whisperx  # noqa: F401
        except ImportError:
            raise e          # no local fallback in the cloud — surface the real error
        if provider == "grok":
            print(f"      ! {e}. Falling back to local WhisperX...")
            result = transcribe.transcribe(meta["audio_path"], provider="whisperx", usage_ctx=usage_ctx)
        else:
            raise
    path = os.path.join(output_root, f"{meta['id']}.transcript.json")
    json.dump({**meta, **result}, open(path, "w"), indent=2)
    print(f"      transcript -> {path} ({len(result['words'])} words)")
    return path


def _transcribe(url, provider, output_root="output", usage_ctx=None):
    print(f"[0/5] Fetching audio: {url}")
    meta = fetch.fetch_audio(url)
    m, s = meta["duration_sec"] // 60, meta["duration_sec"] % 60
    print(f"      {meta['title']}  ({m}m {s}s)")
    print(f"[1/5] Transcribing via {provider}")
    try:
        result = transcribe.transcribe(meta["audio_path"], provider=provider, usage_ctx=usage_ctx)
    except Exception as e:
        try:
            import whisperx  # noqa: F401
        except ImportError:
            raise e          # no local fallback in the cloud — surface the real error
        if provider == "grok":
            print(f"      ! {e}. Falling back to local WhisperX...")
            result = transcribe.transcribe(meta["audio_path"], provider="whisperx", usage_ctx=usage_ctx)
        else:
            raise
    path = os.path.join(output_root, f"{meta['id']}.transcript.json")
    json.dump({**meta, **result}, open(path, "w"), indent=2)
    print(f"      transcript -> {path} ({len(result['words'])} words)")
    return path


def run(url=None, provider="grok", transcript=None, source=None,
        use_llm=True, max_sec=35.0, safe_only=False, count=5, make_audiogram=False,
        series=None, end_screen=True, source_file=None, episode_id=None,
        progress_cb=None, output_root="output", reframe_mode="speaker",
        guest_name=None, moments=None, usage_ctx=None):
    """Run the pipeline. Returns a structured result dict (see the Studio integration
    spec). `progress_cb(stage, pct, msg)` is called at each stage for live progress;
    `output_root` roots all written files (use a temp dir from a worker)."""
    def _p(stage, pct, msg=""):
        if progress_cb:
            try:
                progress_cb(stage, pct, msg)
            except Exception:
                pass

    os.makedirs(os.path.join(output_root, "final"), exist_ok=True)

    # Stages 0-1. A local master (e.g. downloaded from Google Drive) skips YouTube
    # entirely: transcribe from the file and cut from it locally.
    _p("fetch", 0, "fetching + transcribing")
    # Brief 2 (transcribe once, reuse everywhere): a supplied transcript is the
    # app's stored AssemblyAI words (worker/app.py already validated it against the
    # media). When present we reuse it and NEVER run Grok STT; only a missing or
    # rejected transcript falls through to _transcribe below.
    reused = bool(transcript)
    if source_file:
        tpath = transcript or _transcribe_local(source_file, provider, episode_id, output_root, usage_ctx=usage_ctx)
        source = source or source_file        # cut locally from the master
    else:
        tpath = transcript or _transcribe(url, provider, output_root, usage_ctx=usage_ctx)
    tdata = json.load(open(tpath))
    # Record which transcription path actually ran (reported back in the result and
    # fed to the cost log). Reuse logs a $0 row so the saving is visible; the STT
    # paths already logged their own cost row inside _transcribe.
    if reused:
        transcript_source = "reuse_assemblyai"
        transcribe.log_reuse(tdata.get("words"), usage_ctx)
    else:
        transcript_source = "whisperx" if str(tdata.get("provider")) == "whisperx" else "grok_stt"
    print(f"[1/5] transcript_source = {transcript_source}")

    cpath = tpath.replace(".transcript.json", ".clips.json")
    if moments:
        # Exact-cut (Wave 1): render the board-approved windows instead of
        # auto-selecting. The worker RE-RUNS its own safety gate on each window
        # (moments_mod uses detect.assess_safety), so the trauma-informed gate is
        # never bypassed. Auto-select stays the default when `moments` is absent.
        _p("detect", 35, "cutting approved moments")
        print(f"[2/5] Exact-cut: rendering {len(moments)} approved moment(s)")
        clips = moments_mod.build_moment_clips(
            tdata, moments, max_sec=max_sec, episode_id=episode_id)
        result = {"clips": clips, "title": tdata.get("title", ""),
                  "candidate_pool": [], "source": "exact_cut"}
        json.dump(result, open(cpath, "w"), indent=2)
        n_ok = sum(1 for c in clips if c.get("safety", "ok") == "ok")
        print(f"      {len(clips)} moment(s) resolved ({n_ok} ok, "
              f"{len(clips) - n_ok} flagged for review)  -> {cpath}")
    else:
        # Stage 2, detect (auto-select)
        _p("detect", 35, "finding Kintsugi moments")
        print(f"[2/5] Detecting Kintsugi moments (top {count})"
              + ("" if use_llm else " (heuristic only)"))
        result = detect.detect(tpath, use_llm=use_llm, top_n=count, usage_ctx=usage_ctx)
        json.dump(result, open(cpath, "w"), indent=2)
        n_ok = sum(1 for c in result["clips"] if c.get("safety", "ok") == "ok")
        print(f"      {len(result['clips'])} moments ({n_ok} ok, "
              f"{len(result['clips']) - n_ok} flagged for review)  -> {cpath}")
        # Never pad with weak/unsafe clips — if fewer than asked survived, say so.
        if len(result["clips"]) < count:
            print(f"      note: only {len(result['clips'])} clean moment(s) cleared the "
                  f"gate (asked for {count}). Shipping what's clean, not padding.")

    # Stage 2.7 — per-clip metadata pack (title/description/hashtags/pinned/banner).
    # Non-fatal: if Grok or the key is unavailable, the videos still cut and the
    # producer fills metadata by hand.
    if use_llm and result["clips"]:
        _p("metadata", 50, "writing metadata packs")
        try:
            ep_url = f"https://www.youtube.com/watch?v={tdata.get('id')}"
            metadata.generate(result["clips"],
                              result.get("title") or tdata.get("title", ""), ep_url,
                              guest_name=guest_name, series=series, usage_ctx=usage_ctx)
            json.dump(result, open(cpath, "w"), indent=2)   # persist metadata
            n_meta = sum(1 for c in result["clips"] if c.get("metadata"))
            print(f"      metadata packs: {n_meta}/{len(result['clips'])} -> {cpath}")
        except Exception as e:
            print(f"      ! metadata pack skipped: {str(e)[:160]}")
            print("        (videos still cut; fill title/description by hand)")

    # Stage 3 — cut
    _p("cut", 60, "cutting clips")
    print("[3/5] Cutting clips")
    manifest = cut.run(cpath, source=source, safe_only=safe_only, max_sec=max_sec)
    cuts = json.load(open(manifest))["cuts"]
    if not cuts:
        print("      no clips cut (check source/network). Stopping.")
        _p("done", 100, "no clips cut")
        return {"episode_id": tdata.get("id"), "title": result.get("title"),
                "series": series, "guest_name": guest_name, "clips": [],
                "review_md_path": None, "transcript_path": tpath,
                "candidate_pool": result.get("candidate_pool", []),
                "transcript_source": transcript_source}
    words_all = tdata["words"]

    # Per-episode output bundle: <output_root>/final/<id>/ holds clips + REVIEW.md
    ep_id = tdata.get("id") or result.get("source") or "episode"
    final_dir = os.path.join(output_root, "final", ep_id)
    os.makedirs(final_dir, exist_ok=True)
    # Map clip_id -> on-screen hook banner (from the metadata pack).
    banner_by_id = {c.get("clip_id"): (c.get("metadata") or {}).get("banner_hook")
                    for c in result["clips"]}
    # Map clip_id -> audiogram footer title + spoken-line caption (from the metadata pack;
    # the audiogram uses the curated short title and the clip's hook line).
    title_by_id = {c.get("clip_id"): (c.get("metadata") or {}).get("title")
                   for c in result["clips"]}
    hookline_by_id = {c.get("clip_id"): c.get("hook_line") for c in result["clips"]}

    # Stages 4-5 — reframe + caption/CTA/logo per clip
    _p("render", 70, "reframing + captioning + branding")
    print("[4/5+5/5] Reframing + captioning + branding")
    finals = []
    for i, c in enumerate(cuts):
        _p("render", 70 + int(25 * i / max(1, len(cuts))),
           f"clip {i + 1}/{len(cuts)}")
        cut_file = os.path.join(os.path.dirname(cpath) or ".", c["file"])
        vertical = os.path.join(final_dir, f"{c['clip_id']}_v.mp4")
        try:
            framing = reframe.reframe(cut_file, vertical,
                                      guest=result.get("guest_speaker"), mode=reframe_mode)
            c["framing"] = framing                    # carry into REVIEW.md
            words = caption.clip_words(words_all, c["start"], c["end"])
            outs = caption.finish(vertical, words, os.path.join(final_dir, c["clip_id"]),
                                  banner=banner_by_id.get(c["clip_id"]))
            # Branded end screen: dissolve each finished Short into the outro,
            # alternating the two options across clips (clip i -> option i%2).
            if end_screen and endscreen.available():
                opt = None
                for f in outs:
                    opt = endscreen.append_to(f, i)
                c["end_screen"] = opt
            finals.extend(outs)
            # Opt-in branded audiograms (square + vertical) from the clip's audio.
            if make_audiogram:
                a_outs = audiogram.render(cut_file, words,
                                          os.path.join(final_dir, c["clip_id"]),
                                          series=series,
                                          caption=hookline_by_id.get(c["clip_id"]),
                                          title=title_by_id.get(c["clip_id"]),
                                          guest_name=guest_name)
                finals.extend(a_outs)
        except Exception as e:
            print(f"      ! {c['clip_id']} failed: {str(e)[:160]}")
        finally:
            try:                       # tidy the intermediate; never fatal
                if os.path.exists(vertical):
                    os.remove(vertical)
            except OSError:
                pass

    # Stage 6 — producer gate sheet. Carry framing flags back onto the picks too.
    framing_by_id = {c["clip_id"]: c.get("framing") for c in cuts}
    for clip in result["clips"]:
        if clip.get("clip_id") in framing_by_id:
            clip["framing"] = framing_by_id[clip["clip_id"]]
    json.dump(result, open(cpath, "w"), indent=2)        # clips.json now complete
    review_path = review.write_review(final_dir, result, cuts)

    print(f"\nDONE — {len(finals)} files in {final_dir}/")
    flagged = [c["clip_id"] for c in cuts
               if c.get("safety", "ok") != "ok" or (c.get("framing") and c["framing"] != "ok")]
    if flagged:
        print(f"Producer review before publishing: {', '.join(flagged)}")
    print(f"Review gate sheet -> {review_path}")
    print("Each clip has a _shorts (arrows) and _universal (Reels/TikTok) version.")
    _p("done", 100, f"{len(finals)} files")

    # Structured result (the contract the Studio worker uploads + displays).
    cut_by_id = {c["clip_id"]: c for c in cuts}
    clips_out = []
    for clip in result["clips"]:
        cid = clip.get("clip_id")
        base = os.path.join(final_dir, cid)
        candidates = {
            "shorts": f"{base}_shorts.mp4",
            "universal": f"{base}_universal.mp4",
            "audiogram_square": f"{base}_audiogram_square.mp4",
            "audiogram_vertical": f"{base}_audiogram_vertical.mp4",
        }
        files = {k: v for k, v in candidates.items() if os.path.exists(v)}
        cut_for = cut_by_id.get(cid, {})
        clips_out.append({
            "clip_id": cid,
            "start": cut_for.get("start", clip.get("start")),
            "end": cut_for.get("end", clip.get("end")),
            "length_sec": cut_for.get("duration", clip.get("length_sec")),
            "archetype": clip.get("archetype"),
            "hook_line": clip.get("hook_line"),
            "why": clip.get("why", ""),
            "hook_formula": clip.get("hook_formula", "none"),
            "loopable": clip.get("loopable", False),
            "safety": clip.get("safety", "ok"),
            "safety_note": clip.get("safety_note", ""),
            "framing": clip.get("framing", "ok"),
            "metadata": clip.get("metadata", {}),
            "files": files,
        })
    return {
        "episode_id": ep_id,
        "title": result.get("title"),
        "series": series,
        "guest_name": guest_name,
        "clips": clips_out,
        "review_md_path": review_path,
        "transcript_path": tpath,            # worker persists this for per-clip ops
        "candidate_pool": result.get("candidate_pool", []),
        "transcript_source": transcript_source,   # reuse_assemblyai | grok_stt | whisperx
    }


def render_clip(spec, url=None, source=None, words_all=None, series=None,
                guest_name=None, reframe_mode="speaker", make_audiogram=True,
                end_screen=True, index=0, output_root="output",
                with_metadata=False, episode_title="", episode_url="", usage_ctx=None):
    """Cut + reframe + caption + brand ONE moment, returning a clip dict in the same
    shape as run()'s `clips[]` entries (clip_id, start, end, files, framing, ...).

    The per-clip "reframe"/"replace" buttons use this: same renderers as the full job,
    just one moment. `spec` needs clip_id + start + end (and ideally hook_line/archetype/
    text/safety). `with_metadata=True` writes a fresh metadata pack (the "replace" path);
    reframe passes the existing pack in `spec["metadata"]` so captions/banner stay stable.
    Reframe-only re-cuts the SAME start/end with a new crop mode; the caller swaps files."""
    os.makedirs(os.path.join(output_root, "clips"), exist_ok=True)
    final_dir = os.path.join(output_root, "final", "clip")
    os.makedirs(final_dir, exist_ok=True)

    cid = spec["clip_id"]
    start, end = float(spec["start"]), float(spec["end"])
    # Same hard ceiling as the full pipeline (trim the END, keep the hook).
    if end - start > cut.MAX_CLIP_SEC:
        end = start + cut.MAX_CLIP_SEC

    cut_file = os.path.join(output_root, "clips", f"{cid}.mp4")
    if source:
        cut.cut_local(source, start, end, cut_file)
    else:
        cut.cut_remote(url, start, end, cut_file)

    meta = dict(spec.get("metadata") or {})
    if with_metadata:
        try:
            one = [{"hook_line": spec.get("hook_line", ""),
                    "archetype": spec.get("archetype", ""),
                    "why": spec.get("why", ""), "text": spec.get("text", ""),
                    "safety": spec.get("safety", "ok")}]
            metadata.generate(one, episode_title, episode_url, guest_name=guest_name, series=series, usage_ctx=usage_ctx)
            meta = one[0].get("metadata", meta)
        except Exception as e:
            print(f"      ! metadata pack skipped: {str(e)[:160]}")
    banner = meta.get("banner_hook")

    vertical = os.path.join(final_dir, f"{cid}_v.mp4")
    framing = reframe.reframe(cut_file, vertical, guest=guest_name, mode=reframe_mode)
    words = caption.clip_words(words_all or [], start, end)
    outs = caption.finish(vertical, words, os.path.join(final_dir, cid), banner=banner)
    if end_screen and endscreen.available():
        for f in outs:
            endscreen.append_to(f, index)
    if make_audiogram:
        try:
            audiogram.render(cut_file, words, os.path.join(final_dir, cid), series=series,
                             caption=spec.get("hook_line"), title=meta.get("title"),
                             guest_name=guest_name)
        except Exception as e:
            print(f"      ! audiogram skipped: {str(e)[:160]}")
    for p in (cut_file, vertical):                 # tidy intermediates
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass

    files = {}
    for kind, suffix in (("shorts", "_shorts.mp4"), ("universal", "_universal.mp4"),
                         ("audiogram_square", "_audiogram_square.mp4"),
                         ("audiogram_vertical", "_audiogram_vertical.mp4")):
        p = os.path.join(final_dir, f"{cid}{suffix}")
        if os.path.exists(p):
            files[kind] = p
    return {
        "clip_id": cid,
        "start": round(start, 2),
        "end": round(end, 2),
        "length_sec": round(end - start, 1),
        "archetype": spec.get("archetype"),
        "hook_line": spec.get("hook_line"),
        "why": spec.get("why", ""),
        "hook_formula": spec.get("hook_formula", "none"),
        "loopable": spec.get("loopable", False),
        "safety": spec.get("safety", "ok"),
        "safety_note": spec.get("safety_note", ""),
        "framing": framing,
        "metadata": meta,
        "files": files,
    }


def main():
    ap = argparse.ArgumentParser(description="Turn an episode into finished KH clips.")
    ap.add_argument("url", nargs="?", help="YouTube URL (omit if using --transcript)")
    ap.add_argument("--provider", default="grok", choices=["grok", "whisperx"])
    ap.add_argument("--transcript", default=None, help="existing transcript.json (skip 0-1)")
    ap.add_argument("--source", default=None, help="local source video (skip download in cut)")
    ap.add_argument("--no-llm", action="store_true", help="heuristic detect only (no Grok)")
    ap.add_argument("--safe-only", action="store_true", help="skip clips flagged 'review'")
    ap.add_argument("--max-sec", type=float, default=35.0, help="hard max clip length (default 35)")
    ap.add_argument("--count", type=int, default=5, help="finished Shorts per episode (default 5)")
    ap.add_argument("--audiogram", action="store_true",
                    help="also render branded audiograms (square + vertical) per clip")
    ap.add_argument("--series", default=None,
                    help="series name for audiogram artwork -> assets/artwork/<series>.png")
    ap.add_argument("--no-endscreen", action="store_true",
                    help="skip the branded end screen on each clip")
    args = ap.parse_args()
    if not args.url and not args.transcript:
        ap.error("give a YouTube URL or --transcript")
    run(url=args.url, provider=args.provider, transcript=args.transcript,
        source=args.source, use_llm=not args.no_llm,
        max_sec=args.max_sec, safe_only=args.safe_only, count=args.count,
        make_audiogram=args.audiogram, series=args.series,
        end_screen=not args.no_endscreen)


if __name__ == "__main__":
    main()
