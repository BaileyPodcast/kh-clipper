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
    4 reframe    16:9 -> 9:16 (writes a face-band sidecar, KH-MGX-001 1.3)
    5 caption    Olive Pill KINETIC captions (pop-in, highlight word, punch-in,
                 face-aware placement, CALM preset on review clips) + gentle
                 CTAs + logo, dual export. No appended end screen (KH-MGX-001 1.6)
                 — src/endscreen.py stays in the repo for non-Shorts outputs.

Output: output/final/<clip_id>_shorts.mp4  and  _universal.mp4
"""
import argparse
import concurrent.futures
import json
import os
import threading

from src import (fetch, transcribe, detect, metadata, cut, reframe, caption,
                 review, audiogram, kinetic, tighten,
                 moments as moments_mod)

# Per-clip rendering (Stages 4-5, in run()) runs CONCURRENTLY across clips: each
# clip's work is independent once cut, and every step is an ffmpeg (or Node, for
# kinetic captions) subprocess call that releases the GIL while it runs, so a
# thread pool gives real wall-clock overlap without needing multiprocessing.
# Matched to the Modal worker's `cpu=` request (worker/app.py JOB_CPU) -- more
# threads than requested cores just means more ffmpeg processes fighting over
# the same CPU budget, not more throughput. Override via KH_CLIP_WORKERS for a
# local run on a bigger/smaller machine (`python clipper.py URL` with no worker
# involved at all).
CLIP_RENDER_WORKERS = int(os.environ.get("KH_CLIP_WORKERS", "4"))


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


def _finish(vertical, words, out_base, banner, highlight_word, safety, clip_index,
            caption_style="classic", loopable=False):
    """Branch at the finish stage (KH-MGX-001 Wave 2): 'kinetic' renders via
    the Remotion premium layer (src.kinetic); 'classic' (default) is the
    existing libass path (src.caption), unchanged. A kinetic failure never
    costs the clip -- falls back to classic, the same best-effort pattern
    already used throughout this pipeline (reframe's pan fallback, Wave 1's
    punch-in fallback). `loopable` (from the rerank result) lets both styles
    drop the CTA end cards on a short seamless-loop clip."""
    if caption_style == "kinetic":
        # One retry before falling back: the Remotion render can die to a
        # transient headless-chrome crash in the container, and a whole job
        # silently shipping classic because of one flake is exactly the
        # "looks like the old clips" failure the 2026-08-18 live test hit.
        kin_err = None
        for attempt in (1, 2):
            try:
                outs = kinetic.finish(vertical, words, out_base, banner=banner,
                                      highlight_word=highlight_word, safety=safety,
                                      loopable=loopable)
                return outs, {"caption_engine": "kinetic"}
            except Exception as e:
                kin_err = str(e)[-300:]
                print(f"      ~ kinetic render failed (attempt {attempt}: {str(e)[:160]})")
        print("      ~ falling back to classic captions")
        outs = caption.finish(vertical, words, out_base, banner=banner,
                              highlight_word=highlight_word, safety=safety, clip_index=clip_index,
                              loopable=loopable)
        return outs, {"caption_engine": "classic_fallback", "kinetic_error": kin_err}
    outs = caption.finish(vertical, words, out_base, banner=banner,
                          highlight_word=highlight_word, safety=safety, clip_index=clip_index,
                          loopable=loopable)
    return outs, {"caption_engine": "classic"}


def run(url=None, provider="grok", transcript=None, source=None,
        use_llm=True, max_sec=35.0, safe_only=False, count=5, make_audiogram=False,
        series=None, source_file=None, episode_id=None,
        progress_cb=None, output_root="output", reframe_mode="speaker",
        guest_name=None, moments=None, usage_ctx=None, caption_style="classic",
        clip_type="best", reviewer_anchors=None, hook_phrases=None,
        clip_types=None):
    """Run the pipeline. Returns a structured result dict (see the Studio integration
    spec). `progress_cb(stage, pct, msg)` is called at each stage for live progress;
    `output_root` roots all written files (use a temp dir from a worker).

    `clip_types` (KH-CTP-001 Phase 2, optional list of 2+ type keys): when
    given (and `moments` is not), runs a SPREAD instead of `count` clips of
    one `clip_type` — one genuine top pick per listed type, trimmed to
    `count` entries, each clip tagged with its own real type. A type with no
    genuine moment for this episode is skipped, not padded. `clip_type` is
    ignored in this mode. None (default) = the existing single-type path,
    byte-identical."""
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
        # KH-CTP-001: stamp the job's clip type on exact-cut clips too, so the
        # metadata pack and the echoed outputs carry it (brief section C).
        for c in clips:
            c["clip_type"] = clip_type
        result = {"clips": clips, "title": tdata.get("title", ""),
                  "candidate_pool": [], "source": "exact_cut"}
        json.dump(result, open(cpath, "w"), indent=2)
        n_ok = sum(1 for c in clips if c.get("safety", "ok") == "ok")
        print(f"      {len(clips)} moment(s) resolved ({n_ok} ok, "
              f"{len(clips) - n_ok} flagged for review)  -> {cpath}")
    elif clip_types:
        # KH-CTP-001 Phase 2: a natural spread across distinct type lenses in
        # one run, trimmed to `count` types, instead of `count` clips of one
        # repeated type.
        spread_order = list(clip_types)[:max(1, count)]
        _p("detect", 35, "finding a spread of Kintsugi moments")
        print(f"[2/5] Detecting a spread across {len(spread_order)} type(s): "
              f"{', '.join(spread_order)}"
              + ("" if use_llm else " (heuristic only)"))
        result = detect.detect_spread(tpath, use_llm=use_llm, types=spread_order,
                                      usage_ctx=usage_ctx, reviewer_anchors=reviewer_anchors,
                                      audio_path=tdata.get("audio_path"))
        json.dump(result, open(cpath, "w"), indent=2)
        n_ok = sum(1 for c in result["clips"] if c.get("safety", "ok") == "ok")
        print(f"      {len(result['clips'])}/{len(spread_order)} requested types produced "
              f"a clip ({n_ok} ok, {len(result['clips']) - n_ok} flagged for review)  -> {cpath}")
    else:
        # Stage 2, detect (auto-select)
        _p("detect", 35, "finding Kintsugi moments")
        print(f"[2/5] Detecting Kintsugi moments (top {count})"
              + ("" if use_llm else " (heuristic only)"))
        result = detect.detect(tpath, use_llm=use_llm, top_n=count, usage_ctx=usage_ctx,
                               clip_type=clip_type, reviewer_anchors=reviewer_anchors,
                               audio_path=tdata.get("audio_path"))
        json.dump(result, open(cpath, "w"), indent=2)
        n_ok = sum(1 for c in result["clips"] if c.get("safety", "ok") == "ok")
        print(f"      {len(result['clips'])} moments ({n_ok} ok, "
              f"{len(result['clips']) - n_ok} flagged for review)  -> {cpath}")
        # Never pad with weak/unsafe clips — if fewer than asked survived, say so.
        if len(result["clips"]) < count:
            print(f"      note: only {len(result['clips'])} clean moment(s) cleared the "
                  f"gate (asked for {count}). Shipping what's clean, not padding.")

    # The lens this run actually used: "spread" for a spread run, the exact-cut/
    # single-type branch's own clip_type otherwise. Read from `result` (both
    # branches set it) so this never drifts from what detect()/detect_spread()
    # actually reported.
    effective_clip_type = result.get("clip_type", clip_type)

    # Stage 2.7 — per-clip metadata pack (title/description/hashtags/pinned/banner).
    # Non-fatal: if Grok or the key is unavailable, the videos still cut and the
    # producer fills metadata by hand.
    if use_llm and result["clips"]:
        _p("metadata", 50, "writing metadata packs")
        try:
            # The source id is a Drive/file id, not a YouTube video id, so we do
            # NOT build a watch URL from it (that link is dead). Leave it blank so
            # the description carries the placeholder; the app fills the real
            # published YouTube link at upload time.
            ep_url = ""
            metadata.generate(result["clips"],
                              result.get("title") or tdata.get("title", ""), ep_url,
                              guest_name=guest_name, series=series, usage_ctx=usage_ctx,
                              clip_type=effective_clip_type, hook_phrases=hook_phrases)
            json.dump(result, open(cpath, "w"), indent=2)   # persist metadata
            n_meta = sum(1 for c in result["clips"] if c.get("metadata"))
            print(f"      metadata packs: {n_meta}/{len(result['clips'])} -> {cpath}")
        except Exception as e:
            print(f"      ! metadata pack skipped: {str(e)[:160]}")
            print("        (videos still cut; fill title/description by hand)")

    words_all = tdata["words"]

    # 10k standard: pre-compute each clip's silence-tightening plan (pure, no
    # ffmpeg — see src/tighten.py) BEFORE cutting, and attach it to the clip
    # entry, so cut.run() can fold cut+tighten into ONE ffmpeg pass per clip
    # instead of a full cut followed by a full second tighten re-encode (see
    # src/cut.py cut_local_tightened + its module docstring). Local source
    # only — combining tighten with the remote (YouTube) per-clip fetch is a
    # different, riskier problem and stays the existing two-pass path below.
    # Plans against exactly the window cut.run() will actually cut (same
    # cut.resolve_window call cut.run() itself uses), so there's no drift
    # between what gets planned and what gets cut.
    if source:
        for c in result["clips"]:
            try:
                w_start, w_end, _ = cut.resolve_window(
                    float(c["start"]), float(c["end"]), max_sec)
                plan = tighten.plan_tighten(words_all, w_start, w_end,
                                            c.get("safety", "ok"))
                if plan:
                    c["_tighten_plan"] = plan
            except (KeyError, TypeError, ValueError):
                pass                      # malformed clip -- cut.run() will skip it too
        json.dump(result, open(cpath, "w"), indent=2)

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
                "transcript_source": transcript_source, "caption_style": caption_style,
                "clip_type": effective_clip_type,
                "spread_types": result.get("spread_types"), "spread_report": result.get("spread_report")}

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
    # Map clip_id -> loopable (from the rerank result) so a short seamless-loop
    # clip ships with no CTA end cards over its loop seam.
    loopable_by_id = {c.get("clip_id"): bool(c.get("loopable", False))
                      for c in result["clips"]}

    # Stages 4-5 — reframe + caption/CTA/logo per clip. Rendered CONCURRENTLY
    # (see CLIP_RENDER_WORKERS above): each clip is fully self-contained after
    # cutting (its own files, its own dict entry in `cuts`/`result["clips"]"),
    # so the ONLY shared mutable state across threads is `finals` (appended
    # under a lock below) and the progress counter. Per-clip fault isolation
    # (one clip's exception never aborts the others) and the exact per-clip
    # log lines are unchanged from the old sequential loop -- just now run
    # from `_render_one`, one call per clip, dispatched to a thread pool.
    _p("render", 70, "reframing + captioning + branding")
    print("[4/5+5/5] Reframing + captioning + branding")
    finals = []
    progress_lock = threading.Lock()
    done = [0]                            # mutable counter threads can share

    def _render_one(i, c):
        cut_file = os.path.join(os.path.dirname(cpath) or ".", c["file"])
        vertical = os.path.join(final_dir, f"{c['clip_id']}_v.mp4")
        clip_finals = []
        try:
            # Silence tightening (10k standard): compress technical dead air inside
            # the clip, never a loaded pause, never a CALM clip. When cut.run()
            # already combined cut+tighten into one ffmpeg pass (local source --
            # see src/cut.py cut_local_tightened), `cut_file` IS the tightened
            # clip already and the plan travels with it (`tighten_plan`); only
            # the word/speech timings below still need remapping. Otherwise this
            # is still the original two-pass path: tighten runs on the cut file
            # BEFORE reframe so face tracking and captions see the same frames.
            plan = c.get("tighten_plan")
            if plan is None:
                plan = tighten.plan_tighten(words_all, c["start"], c["end"],
                                            c.get("safety", "ok"))
            if plan and c.get("tightened"):
                print(f"      ~ {c['clip_id']} tightened (combined pass): "
                      f"{tighten.total_removed(plan)}s of dead air compressed")
            elif plan:
                tightened = cut_file.replace(".mp4", "_tight.mp4")
                try:
                    tighten.apply_tighten(cut_file, plan, tightened)
                    cut_file = tightened
                    print(f"      ~ {c['clip_id']} tightened: "
                          f"{tighten.total_removed(plan)}s of dead air compressed")
                except Exception as e:
                    plan = []
                    print(f"      ~ tighten skipped ({str(e)[:120]})")
            # Diarized speech windows: tell reframe WHEN the hero speaks inside this
            # clip so face-follow locks onto the right person on a two-shot. On a
            # tightened clip the windows are remapped into the tightened clock so
            # face-follow reads the same frames the file actually contains.
            speech = reframe.speech_windows(words_all, c["start"], c["end"],
                                            result.get("guest_speaker"))
            if plan and speech:
                speech = {k: [(tighten.remap(t0, plan), tighten.remap(t1, plan))
                              for (t0, t1) in v] for k, v in speech.items()}
            framing = reframe.reframe(cut_file, vertical,
                                      guest=result.get("guest_speaker"), mode=reframe_mode,
                                      speech=speech)
            c["framing"] = framing                    # carry into REVIEW.md
            words = caption.clip_words(words_all, c["start"], c["end"])
            if plan:
                words = tighten.remap_words(words, plan)
            # Kinetic captions (KH-MGX-001): highlight_word + safety (-> CALM preset
            # for anything not "ok") + clip index (alternates the punch-in direction).
            # caption_style picks classic (libass, Wave 1) or kinetic (Remotion, Wave 2).
            outs, finish_info = _finish(vertical, words, os.path.join(final_dir, c["clip_id"]),
                          banner=banner_by_id.get(c["clip_id"]),
                          highlight_word=c.get("highlight_word"),
                          safety=c.get("safety", "ok"), clip_index=i,
                          caption_style=caption_style,
                          loopable=loopable_by_id.get(c["clip_id"], False))
            c.update(finish_info)                     # caption_engine (+ kinetic_error)
            # No appended end screen (1.6, decided 2026-08-05) — an appended outro
            # broke the loop rerank.py rewards. The final frame is real story
            # footage; the brand moment lives in the CTA end cards instead.
            clip_finals.extend(outs)
            # Opt-in branded audiograms (square + vertical) from the clip's audio.
            if make_audiogram:
                a_outs = audiogram.render(cut_file, words,
                                          os.path.join(final_dir, c["clip_id"]),
                                          series=series,
                                          caption=hookline_by_id.get(c["clip_id"]),
                                          title=title_by_id.get(c["clip_id"]),
                                          guest_name=guest_name)
                clip_finals.extend(a_outs)
        except Exception as e:
            print(f"      ! {c['clip_id']} failed: {str(e)[:160]}")
        finally:
            try:                       # tidy the intermediates; never fatal
                if os.path.exists(vertical):
                    os.remove(vertical)
                sidecar = vertical + ".faceband.json"
                if os.path.exists(sidecar):
                    os.remove(sidecar)
            except OSError:
                pass
        with progress_lock:
            done[0] += 1
            _p("render", 70 + int(25 * done[0] / max(1, len(cuts))),
               f"clip {done[0]}/{len(cuts)}")
        return clip_finals

    workers = max(1, min(CLIP_RENDER_WORKERS, len(cuts)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        # Submitted concurrently; .result() below just waits for each in turn
        # (they're all already running) -- collecting results back on the main
        # thread only, so `finals` itself needs no lock.
        futures = [pool.submit(_render_one, i, c) for i, c in enumerate(cuts)]
        for fut in futures:
            finals.extend(fut.result())

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
            "clip_type": clip.get("clip_type", effective_clip_type),   # echoed on every clip (KH-CTP-001)
            "archetype": clip.get("archetype"),
            "hook_line": clip.get("hook_line"),
            # Selection data for the manifest (locked cross-repo contract):
            # `score` is the final 0-100 fit score, `hook` the hook line,
            # alongside the existing `loopable`. Every existing field stays.
            "score": clip.get("fit_score"),
            "hook": clip.get("hook_line"),
            "why": clip.get("why", ""),
            "hook_formula": clip.get("hook_formula", "none"),
            "loopable": clip.get("loopable", False),
            "safety": clip.get("safety", "ok"),
            "safety_note": clip.get("safety_note", ""),
            "framing": clip.get("framing", "ok"),
            "highlight_word": clip.get("highlight_word", ""),   # KH-MGX-001 1.2
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
        "caption_style": caption_style,      # classic (libass) | kinetic (Remotion, Wave 2)
        "clip_type": effective_clip_type,    # the selection lens this job ran under (KH-CTP-001);
                                              # "spread" for a spread run (Phase 2)
        "spread_types": result.get("spread_types"),     # None outside a spread run
        "spread_report": result.get("spread_report"),   # [{"type","found","reason"}] or None
    }


def render_clip(spec, url=None, source=None, words_all=None, series=None,
                guest_name=None, reframe_mode="speaker", reframe_offset=0.0,
                make_audiogram=True, index=0, output_root="output",
                with_metadata=False, episode_title="", episode_url="", usage_ctx=None,
                caption_style="classic"):
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
    start, end, _ = cut.resolve_window(start, end, cut.MAX_CLIP_SEC)

    # Same silence tightening as the full run, so a reframe/replace re-render
    # ships the same pacing as the original render. Planned BEFORE cutting: on a
    # local source this folds cut+tighten into ONE ffmpeg pass
    # (cut.cut_local_tightened) instead of a full cut immediately followed by a
    # full second tighten re-encode -- this is the interactive reframe/replace
    # path a producer clicks and waits on live, so the extra pass is real
    # wall-clock time in front of them. cut_local_tightened itself degrades to
    # a plain cut when `plan` is empty, so this is a single call either way.
    plan = tighten.plan_tighten(words_all or [], start, end, spec.get("safety", "ok"))

    cut_file = os.path.join(output_root, "clips", f"{cid}.mp4")
    if source:
        try:
            cut.cut_local_tightened(source, start, end, plan, cut_file)
        except Exception as e:
            plan = []
            print(f"      ~ combined cut/tighten failed ({str(e)[:120]}), "
                  f"falling back to a plain cut")
            cut.cut_local(source, start, end, cut_file)
    else:
        # Remote (YouTube) source: combining tighten with the ranged yt-dlp
        # fetch is out of scope (see cut.cut_local_tightened's docstring) --
        # same two-pass path as before.
        cut.cut_remote(url, start, end, cut_file)
        if plan:
            tightened_path = cut_file.replace(".mp4", "_tight.mp4")
            try:
                tighten.apply_tighten(cut_file, plan, tightened_path)
                cut_file = tightened_path
            except Exception as e:
                plan = []
                print(f"      ~ tighten skipped ({str(e)[:120]})")

    meta = dict(spec.get("metadata") or {})
    if with_metadata:
        try:
            one = [{"hook_line": spec.get("hook_line", ""),
                    "archetype": spec.get("archetype", ""),
                    "why": spec.get("why", ""), "text": spec.get("text", ""),
                    "safety": spec.get("safety", "ok")}]
            metadata.generate(one, episode_title, episode_url, guest_name=guest_name,
                              series=series, usage_ctx=usage_ctx,
                              clip_type=spec.get("clip_type", "best"))
            meta = one[0].get("metadata", meta)
        except Exception as e:
            print(f"      ! metadata pack skipped: {str(e)[:160]}")
    banner = meta.get("banner_hook")

    vertical = os.path.join(final_dir, f"{cid}_v.mp4")
    # Re-derive the diarized guest speaker from the stored transcript words (same
    # rule detect.py used on the original job) so a per-clip reframe/replace gets
    # the same hero-locked framing as the full run. `plan` (and `cut_file`) were
    # already resolved above, before cutting.
    speech = reframe.speech_windows(words_all or [], start, end,
                                    detect.identify_guest(words_all or []))
    if plan and speech:
        speech = {k: [(tighten.remap(t0, plan), tighten.remap(t1, plan))
                      for (t0, t1) in v] for k, v in speech.items()}
    framing = reframe.reframe(cut_file, vertical, guest=guest_name, mode=reframe_mode,
                              offset=reframe_offset, speech=speech)
    words = caption.clip_words(words_all or [], start, end)
    if plan:
        words = tighten.remap_words(words, plan)
    # Kinetic captions (KH-MGX-001), same rules as the full run: highlight_word +
    # safety (-> CALM preset) + index (alternates the punch-in direction). No
    # appended end screen (1.6) — the final frame is real story footage.
    # caption_style picks classic (libass, Wave 1) or kinetic (Remotion, Wave 2).
    speech_plan = plan
    outs, finish_info = _finish(vertical, words, os.path.join(final_dir, cid), banner=banner,
                   highlight_word=spec.get("highlight_word"),
                   safety=spec.get("safety", "ok"), clip_index=index,
                   caption_style=caption_style,
                   loopable=bool(spec.get("loopable", False)))
    spec.update(finish_info)                     # caption_engine (+ kinetic_error)
    if make_audiogram:
        try:
            audiogram.render(cut_file, words, os.path.join(final_dir, cid), series=series,
                             caption=spec.get("hook_line"), title=meta.get("title"),
                             guest_name=guest_name)
        except Exception as e:
            print(f"      ! audiogram skipped: {str(e)[:160]}")
    for p in (cut_file, vertical, vertical + ".faceband.json"):    # tidy intermediates
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
        "clip_type": spec.get("clip_type", "best"),   # stable across reframe/replace
        "archetype": spec.get("archetype"),
        "hook_line": spec.get("hook_line"),
        # Selection data (locked cross-repo contract), same fields as run().
        "score": spec.get("fit_score", spec.get("score")),
        "hook": spec.get("hook_line"),
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
    args = ap.parse_args()
    if not args.url and not args.transcript:
        ap.error("give a YouTube URL or --transcript")
    run(url=args.url, provider=args.provider, transcript=args.transcript,
        source=args.source, use_llm=not args.no_llm,
        max_sec=args.max_sec, safe_only=args.safe_only, count=args.count,
        make_audiogram=args.audiogram, series=args.series)


if __name__ == "__main__":
    main()
