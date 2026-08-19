"""
Integration tests for concurrent per-clip rendering in clipper.py run()
(Stages 4-5): clips render in a thread pool, real overlap happens, and one
clip's failure never aborts the others (the same fault isolation the old
sequential per-clip try/except gave). Runs the real pipeline end to end on a
synthetic local source + the exact-cut ("moments") path with use_llm=False,
so it needs no API keys and no network — offline, deterministic, real ffmpeg.

    python -m pytest tests/test_clip_parallel_render.py
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import clipper
from src import reframe as reframe_mod

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available in this environment")


def _make_source(path, seconds=30):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
         "-f", "lavfi", "-i", "sine=frequency=440", "-t", str(seconds),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", path],
        check=True, capture_output=True)


def _fake_transcript(path, video_id, n_words=30, step=1.0):
    words = [{"text": f"word{i}.", "start": round(i * step, 2),
              "end": round(i * step + 0.6, 2), "speaker": 0}
             for i in range(n_words)]
    json.dump({"id": video_id, "title": "Test Episode", "words": words,
              "text": " ".join(w["text"] for w in words)}, open(path, "w"))
    return path


# Three non-overlapping 8s moments, well clear of each other and of the
# episode's tail, so cut/tighten/reframe/caption all have real (if tiny) work
# to do per clip. reframe_mode="center" skips face detection entirely (no
# mediapipe dependency on the test's critical path); use_llm=False skips the
# Anthropic metadata call (no API key needed offline).
MOMENTS = [{"start": 0.0, "end": 8.0}, {"start": 10.0, "end": 18.0},
          {"start": 20.0, "end": 28.0}]


def test_clips_render_concurrently(tmp_path, monkeypatch):
    src = str(tmp_path / "src.mp4")
    _make_source(src, seconds=30)
    tpath = _fake_transcript(str(tmp_path / "ep.transcript.json"), "vidXYZ")

    # Prove real overlap: track how many reframe.reframe calls are in flight
    # at once. A sequential loop could never show more than 1.
    active = {"n": 0, "max": 0}
    lock = threading.Lock()
    real_reframe = reframe_mod.reframe

    def spy_reframe(*a, **k):
        with lock:
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
        try:
            time.sleep(0.2)              # long enough for 3 threads to overlap
            return real_reframe(*a, **k)
        finally:
            with lock:
                active["n"] -= 1

    # clipper.py holds `reframe` as a module reference (`from src import ...
    # reframe ...`) -- the SAME module object as reframe_mod, so patching it
    # here is visible to clipper.run()'s internals too.
    monkeypatch.setattr(reframe_mod, "reframe", spy_reframe)

    result = clipper.run(
        source_file=src, episode_id="vidXYZ", transcript=tpath,
        moments=MOMENTS, use_llm=False, make_audiogram=False,
        reframe_mode="center", output_root=str(tmp_path / "out"),
        caption_style="classic",
    )

    assert active["max"] >= 2, (
        f"expected overlapping reframe calls, only saw {active['max']} concurrent")
    assert len(result["clips"]) == 3
    for c in result["clips"]:
        assert c["files"].get("shorts"), f"clip {c['clip_id']} produced no shorts file"
        assert os.path.exists(c["files"]["shorts"])
        assert c["files"].get("universal")
        assert os.path.exists(c["files"]["universal"])


def test_one_clip_failure_does_not_abort_the_others(tmp_path, monkeypatch):
    src = str(tmp_path / "src.mp4")
    _make_source(src, seconds=30)
    tpath = _fake_transcript(str(tmp_path / "ep.transcript.json"), "vidABC")

    real_finish = clipper._finish

    def flaky_finish(vertical, words, out_base, *a, **k):
        if out_base.endswith("-02"):          # the second of the 3 moments
            raise RuntimeError("synthetic render failure")
        return real_finish(vertical, words, out_base, *a, **k)

    monkeypatch.setattr(clipper, "_finish", flaky_finish)

    result = clipper.run(
        source_file=src, episode_id="vidABC", transcript=tpath,
        moments=MOMENTS, use_llm=False, make_audiogram=False,
        reframe_mode="center", output_root=str(tmp_path / "out"),
        caption_style="classic",
    )

    assert len(result["clips"]) == 3           # nothing dropped from the manifest
    ok = [c for c in result["clips"] if c["files"].get("shorts")]
    failed = [c for c in result["clips"] if not c["files"].get("shorts")]
    assert len(failed) == 1 and failed[0]["clip_id"].endswith("-02")
    assert len(ok) == 2
    for c in ok:
        assert os.path.exists(c["files"]["shorts"])


def test_combined_cut_tighten_wired_through_the_real_pipeline(tmp_path, monkeypatch):
    """A clip whose window covers a real mid-clip gap gets the combined
    cut+tighten pass end to end (not just at the cut.py unit level): the
    `_tighten_plan` clipper.run() pre-computes gets attached, cut.run() uses
    cut_local_tightened for it, and the manifest comes back marked tightened."""
    src = str(tmp_path / "src.mp4")
    _make_source(src, seconds=35)
    # A 2.8s gap between word 14 and word 15. moments.build_moment_clips SNAPS
    # the requested window to real word boundaries (by word MIDPOINT), so the
    # request below (6.0-26.0) is chosen with margin on both sides: it snaps
    # to [6.0, 26.0] exactly (word6's start .. word23's end), leaving several
    # words before AND after the gap so it lands well clear of both the 4s
    # hook zone and the final-sentence payoff-pause protection.
    words = []
    t = 0.0
    for i in range(30):
        words.append({"text": f"word{i}.", "start": round(t, 2), "end": round(t + 0.6, 2),
                      "speaker": 0})
        t += 1.0
        if i == 14:
            t += 2.4
    tpath = str(tmp_path / "ep.transcript.json")
    json.dump({"id": "vidGAP", "title": "Test Episode", "words": words,
              "text": " ".join(w["text"] for w in words)}, open(tpath, "w"))

    calls = []
    real_tightened = clipper.cut.cut_local_tightened

    def spy_tightened(source, start, end, plan, out_path):
        calls.append(plan)
        return real_tightened(source, start, end, plan, out_path)

    monkeypatch.setattr(clipper.cut, "cut_local_tightened", spy_tightened)

    result = clipper.run(
        source_file=src, episode_id="vidGAP", transcript=tpath,
        moments=[{"start": 6.0, "end": 26.0}], use_llm=False, make_audiogram=False,
        reframe_mode="center", output_root=str(tmp_path / "out"),
        caption_style="classic",
    )

    assert calls, "cut_local_tightened was never called -- combined pass didn't run"
    assert calls[0], "cut_local_tightened was called with an empty plan"
    assert result["clips"][0]["files"].get("shorts")
    assert os.path.exists(result["clips"][0]["files"]["shorts"])
