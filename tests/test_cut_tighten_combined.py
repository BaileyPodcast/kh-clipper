"""
Tests for the combined cut+tighten pass (src/cut.py resolve_window,
_tighten_segments, cut_local_tightened) and cut.run()'s wiring to it: the
"cut + tighten" perf fix that collapses two full ffmpeg re-encodes (cut_local
then tighten.apply_tighten) into one, for a clip with a local source.

    python -m pytest tests/test_cut_tighten_combined.py
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import cut, tighten


# ----------------------------------------------------------------------
# resolve_window: the single source of truth for the MAX_CLIP_SEC trim, used
# by both cut.run() and clipper.py's pre-cut tighten planning, so a plan is
# never built against a window different from the one actually cut.
# ----------------------------------------------------------------------

def test_resolve_window_untrimmed_when_within_cap():
    assert cut.resolve_window(10.0, 30.0, max_sec=35.0) == (10.0, 30.0, False)


def test_resolve_window_trims_the_end_to_the_cap():
    start, end, trimmed = cut.resolve_window(10.0, 60.0, max_sec=35.0)
    assert start == 10.0
    assert end == 45.0
    assert trimmed is True


def test_resolve_window_exactly_at_cap_is_not_trimmed():
    assert cut.resolve_window(0.0, 35.0, max_sec=35.0) == (0.0, 35.0, False)


# ----------------------------------------------------------------------
# _tighten_segments must agree with tighten.apply_tighten's OWN clip-relative
# segment maths, just offset into episode-relative time -- the "kept in sync"
# guarantee both modules' docstrings promise.
# ----------------------------------------------------------------------

PLAN = [(6.0, 8.0, 0.8), (20.0, 22.5, 0.8)]     # two gaps


def _apply_tighten_clip_relative_segments(clip_dur, plan):
    """A copy of tighten.apply_tighten's own internal keep-segment loop (not a
    call into it -- that needs a real ffmpeg file), so this test can assert
    cut._tighten_segments agrees with it once translated by an offset."""
    segments = []
    pos = 0.0
    for gap_start, gap_end, keep_sec in plan:
        segments.append((pos, gap_start + keep_sec))
        pos = gap_end
    segments.append((pos, clip_dur))
    return [(a, b) for a, b in segments if b - a > 0.01]


def test_tighten_segments_matches_apply_tighten_maths_offset_by_start():
    start, end = 100.0, 130.0
    expected = _apply_tighten_clip_relative_segments(end - start, PLAN)
    got = cut._tighten_segments(start, end, PLAN)
    assert len(got) == len(expected)
    for (got_a, got_b), (exp_a, exp_b) in zip(got, expected):
        assert got_a == pytest.approx(start + exp_a)
        assert got_b == pytest.approx(start + exp_b)


def test_tighten_segments_empty_plan_is_the_whole_window():
    assert cut._tighten_segments(5.0, 15.0, []) == [(5.0, 15.0)]


# ----------------------------------------------------------------------
# cut_local_tightened: degrades to a plain cut_local when there's nothing to
# tighten, and builds exactly one ffmpeg filter-graph pass otherwise.
# ----------------------------------------------------------------------

def test_cut_local_tightened_empty_plan_calls_plain_cut_local(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cut, "cut_local", lambda source, start, end, out_path:
                        calls.append(("plain", source, start, end, out_path)))
    monkeypatch.setattr(cut, "_ff", lambda cmd: calls.append(("ff", cmd)))
    out = str(tmp_path / "out.mp4")
    cut.cut_local_tightened("src.mp4", 10.0, 20.0, [], out)
    assert calls == [("plain", "src.mp4", 10.0, 20.0, out)]


def test_cut_local_tightened_with_a_plan_builds_one_filter_graph_pass(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(cut, "_ff", lambda cmd: seen.setdefault("cmd", cmd))
    out = str(tmp_path / "out.mp4")
    cut.cut_local_tightened("src.mp4", 100.0, 130.0, PLAN, out)
    cmd = seen["cmd"]
    assert cmd[:4] == ["ffmpeg", "-y", "-i", "src.mp4"]
    assert "-filter_complex" in cmd
    graph = cmd[cmd.index("-filter_complex") + 1]
    # 3 keep-segments for 2 gaps: one video trim + one audio atrim each.
    assert graph.count("[0:v]trim=start=") == 3
    assert graph.count("[0:a]atrim=start=") == 3
    assert "concat=n=3:v=1:a=1" in graph
    assert "scale=-2:" in graph                  # same scale-to-1080 cut_local applies
    assert cmd.count("-c:v") == 1                 # ONE encode, not two
    assert "medium" in cmd and "18" in cmd
    assert out in cmd


# ----------------------------------------------------------------------
# cut.run() wiring: a `_tighten_plan` attached to a clip routes to
# cut_local_tightened -- local source only, absent/ignored on the remote
# (YouTube) path, and every existing caller (no plan attached) is untouched.
# ----------------------------------------------------------------------

def test_run_uses_combined_pass_when_plan_attached_and_source_is_local(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cut, "cut_local_tightened", lambda source, start, end, plan, out_path:
                        calls.append("tightened"))
    monkeypatch.setattr(cut, "cut_local", lambda source, start, end, out_path:
                        calls.append("plain"))
    clips_json = tmp_path / "ep.clips.json"
    clips_json.write_text(json.dumps({
        "source": "vid1",
        "clips": [{"clip_id": "c-01", "start": 1.0, "end": 20.0,
                   "_tighten_plan": [[6.0, 8.0, 0.8]]}],
    }))
    manifest = cut.run(str(clips_json), source="master.mp4")
    data = json.loads(open(manifest).read())
    assert calls == ["tightened"]
    entry = data["cuts"][0]
    assert entry["tightened"] is True
    assert entry["tighten_plan"] == [[6.0, 8.0, 0.8]]


def test_run_ignores_plan_on_the_remote_path(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cut, "cut_local_tightened", lambda *a, **k: calls.append("tightened"))
    monkeypatch.setattr(cut, "cut_remote", lambda url, start, end, out_path:
                        calls.append("remote"))
    clips_json = tmp_path / "ep.clips.json"
    clips_json.write_text(json.dumps({
        "source": "vid1",
        "clips": [{"clip_id": "c-01", "start": 1.0, "end": 20.0,
                   "source_video_id": "vid1",
                   "_tighten_plan": [[6.0, 8.0, 0.8]]}],
    }))
    manifest = cut.run(str(clips_json))          # no `source` -> remote path
    data = json.loads(open(manifest).read())
    assert calls == ["remote"]
    entry = data["cuts"][0]
    assert "tightened" not in entry
    assert "tighten_plan" not in entry


def test_run_falls_back_to_plain_cut_when_combined_pass_fails(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cut, "cut_local_tightened",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ffmpeg exploded")))
    monkeypatch.setattr(cut, "cut_local", lambda source, start, end, out_path:
                        calls.append("plain"))
    clips_json = tmp_path / "ep.clips.json"
    clips_json.write_text(json.dumps({
        "source": "vid1",
        "clips": [{"clip_id": "c-01", "start": 1.0, "end": 20.0,
                   "_tighten_plan": [[6.0, 8.0, 0.8]]}],
    }))
    manifest = cut.run(str(clips_json), source="master.mp4")
    data = json.loads(open(manifest).read())
    assert calls == ["plain"]                      # clip still produced, not dropped
    entry = data["cuts"][0]
    assert "tightened" not in entry                 # not marked tightened -- it wasn't


def test_run_with_no_plan_is_unchanged(monkeypatch, tmp_path):
    """No `_tighten_plan` attached (every caller before this change) -> the
    exact same routing as before: plain cut_local, no manifest additions."""
    calls = []
    monkeypatch.setattr(cut, "cut_local_tightened", lambda *a, **k: calls.append("tightened"))
    monkeypatch.setattr(cut, "cut_local", lambda source, start, end, out_path:
                        calls.append("plain"))
    clips_json = tmp_path / "ep.clips.json"
    clips_json.write_text(json.dumps({
        "source": "vid1",
        "clips": [{"clip_id": "c-01", "start": 1.0, "end": 20.0}],
    }))
    manifest = cut.run(str(clips_json), source="master.mp4")
    data = json.loads(open(manifest).read())
    assert calls == ["plain"]
    entry = data["cuts"][0]
    assert "tightened" not in entry
    assert "tighten_plan" not in entry


# ----------------------------------------------------------------------
# Real ffmpeg render: the combined single pass lands on the same output
# duration as the old two-pass (cut_local then tighten.apply_tighten) on a
# synthetic source clip. Skipped when ffmpeg/ffprobe aren't available.
# ----------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg/ffprobe not available in this environment")
def test_combined_pass_matches_two_pass_duration_and_frame_size(tmp_path):
    src = str(tmp_path / "src.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
         "-f", "lavfi", "-i", "sine=frequency=440", "-t", "20",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", src],
        check=True, capture_output=True)

    plan = [(6.0, 8.0, 0.8), (12.0, 14.5, 0.8)]

    # Old path: cut_local then tighten.apply_tighten (two full re-encodes).
    old_cut = str(tmp_path / "old_cut.mp4")
    old_out = str(tmp_path / "old_out.mp4")
    cut.cut_local(src, 0.0, 20.0, old_cut)
    tighten.apply_tighten(old_cut, plan, old_out)

    # New path: one combined pass straight from the source.
    new_out = str(tmp_path / "new_out.mp4")
    cut.cut_local_tightened(src, 0.0, 20.0, plan, new_out)

    def probe(path, entries, select_stream=None):
        cmd = ["ffprobe", "-v", "quiet"]
        if select_stream:
            cmd += ["-select_streams", select_stream]
        cmd += ["-show_entries", entries, "-of", "csv=p=0", path]
        return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()

    old_dur = float(probe(old_out, "format=duration"))
    new_dur = float(probe(new_out, "format=duration"))
    # Removed 1.2s + 1.7s = 2.9s of the 20s source either way, within
    # frame-grid slack (same tolerance test_tighten.py's own render test uses).
    assert abs(old_dur - new_dur) < 0.2
    assert abs(new_dur - (20.0 - 2.9)) < 0.5

    old_size = probe(old_out, "stream=width,height", select_stream="v:0")
    new_size = probe(new_out, "stream=width,height", select_stream="v:0")
    assert old_size == new_size                  # same scale-to-1080 behaviour
