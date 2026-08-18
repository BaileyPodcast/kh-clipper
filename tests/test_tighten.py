"""
Tests for silence tightening (src/tighten.py): plan_tighten's hard rules,
the remap maths, remap_words, total_removed, and apply_tighten's segment
building (the ffmpeg render itself is skipped when ffmpeg is absent).

    python -m pytest tests/test_tighten.py
"""
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import tighten


def W(text, start, end):
    return {"text": text, "start": start, "end": end}


# A clip with one 2.0s technical gap mid-clip (after the 4s hook zone) and a
# clean final sentence well clear of the gap.
BASIC_WORDS = [
    W("I", 4.5, 4.8), W("was", 4.9, 5.2), W("lost.", 5.4, 6.0),
    W("Then", 8.0, 8.3), W("everything", 8.4, 8.9), W("changed.", 9.0, 9.6),
    W("Now", 9.9, 10.2), W("I", 10.3, 10.4), W("help", 10.5, 10.9),
    W("others.", 11.0, 11.5),
]


# ----------------------------------------------------------------------
# plan_tighten: hard rules
# ----------------------------------------------------------------------

def test_basic_gap_is_planned():
    plan = tighten.plan_tighten(BASIC_WORDS, 0.0, 12.0, "ok")
    assert plan == [(6.0, 8.0, tighten.KEEP_SEC)]


def test_safety_review_plans_nothing():
    assert tighten.plan_tighten(BASIC_WORDS, 0.0, 12.0, "review") == []
    assert tighten.plan_tighten(BASIC_WORDS, 0.0, 12.0, "exclude") == []


def test_no_gaps_plans_nothing():
    words = [W("I", 4.0, 4.4), W("kept", 4.5, 4.9), W("going.", 5.0, 5.5)]
    assert tighten.plan_tighten(words, 0.0, 10.0, "ok") == []


def test_gap_at_threshold_is_not_planned():
    # Exactly 1.2s is not "longer than 1.2s".
    words = [W("I", 4.0, 4.4), W("waited", 5.6, 6.0), W("there.", 6.1, 6.5)]
    assert tighten.plan_tighten(words, 0.0, 10.0, "ok") == []


def test_hook_zone_is_untouched():
    words = [W("I", 1.0, 1.5), W("froze", 3.5, 3.9),          # gap starts at 1.5
             W("completely", 4.1, 4.6), W("still.", 4.7, 5.2)]
    assert tighten.plan_tighten(words, 0.0, 10.0, "ok") == []


def test_gap_straddling_hook_boundary_is_untouched():
    # Conservative rule: the gap START must clear the 4s hook zone.
    words = [W("I", 3.0, 3.5), W("stopped", 6.0, 6.4), W("running.", 6.5, 7.0)]
    assert tighten.plan_tighten(words, 0.0, 10.0, "ok") == []


def test_payoff_pause_is_preserved():
    # The long gap sits directly before the final sentence: a retention device.
    words = [
        W("I", 4.5, 4.8), W("was", 4.9, 5.2), W("lost.", 5.4, 6.0),
        W("Everything", 6.2, 6.7), W("changed.", 6.8, 7.4),
        W("Now", 9.4, 9.7), W("I", 9.8, 9.9), W("help.", 10.0, 10.5),  # 2.0s gap
    ]
    assert tighten.plan_tighten(words, 0.0, 12.0, "ok") == []


def test_unpunctuated_clip_has_no_protected_payoff_gap():
    # One long unpunctuated sentence: the "final sentence" starts at word 0,
    # which no gap can precede, so an internal gap is fair game.
    words = [W("i", 4.5, 4.8), W("was", 4.9, 5.2), W("lost", 5.4, 6.0),
             W("then", 8.0, 8.3), W("i", 8.4, 8.5), W("healed", 8.6, 9.2)]
    plan = tighten.plan_tighten(words, 0.0, 12.0, "ok")
    assert plan == [(6.0, 8.0, tighten.KEEP_SEC)]


def test_clip_start_rebases_to_clip_relative():
    shifted = [W(w["text"], w["start"] + 100.0, w["end"] + 100.0)
               for w in BASIC_WORDS]
    plan = tighten.plan_tighten(shifted, 100.0, 112.0, "ok")
    assert plan == [(pytest.approx(6.0), pytest.approx(8.0), tighten.KEEP_SEC)]


def test_fifteen_percent_cap_keeps_longest_gaps_first():
    # 20s clip: budget = 3.0s removed. Gaps remove 1.7 + 1.2 + 0.7 = 3.6s, so
    # the two longest fit and the shortest is dropped.
    words = [W("a", 4.0, 4.4), W("b", 6.9, 7.2),      # 2.5s gap, removes 1.7
             W("c", 9.2, 9.5),                        # 2.0s gap, removes 1.2
             W("d", 11.0, 11.3),                      # 1.5s gap, removes 0.7
             W("e", 11.4, 11.8)]
    plan = tighten.plan_tighten(words, 0.0, 20.0, "ok")
    assert plan == [(4.4, 6.9, tighten.KEEP_SEC), (7.2, 9.2, tighten.KEEP_SEC)]
    assert tighten.total_removed(plan) <= 0.15 * 20.0


def test_plan_is_sorted_by_gap_start():
    words = [W("a", 4.0, 4.4), W("b", 6.0, 6.4),      # 1.6s gap (smaller)
             W("c", 9.0, 9.4),                        # 2.6s gap (bigger)
             W("d", 9.5, 9.9)]
    plan = tighten.plan_tighten(words, 0.0, 30.0, "ok")
    assert [g[0] for g in plan] == sorted(g[0] for g in plan)
    assert len(plan) == 2


def test_words_outside_window_are_ignored():
    words = [W("before", 0.0, 50.0)] + BASIC_WORDS + [W("after", 200.0, 201.0)]
    # The stray "before" word overlaps the window start and is clamped; the
    # trailing word is outside entirely. The mid-clip gap is still found.
    plan = tighten.plan_tighten(BASIC_WORDS, 0.0, 12.0, "ok")
    plan2 = tighten.plan_tighten(words[1:], 0.0, 12.0, "ok")
    assert plan == plan2


def test_empty_or_single_word_plans_nothing():
    assert tighten.plan_tighten([], 0.0, 10.0, "ok") == []
    assert tighten.plan_tighten([W("one", 5.0, 5.5)], 0.0, 10.0, "ok") == []
    assert tighten.plan_tighten(BASIC_WORDS, 10.0, 10.0, "ok") == []


# ----------------------------------------------------------------------
# remap and remap_words: pure timestamp maths
# ----------------------------------------------------------------------

PLAN_ONE = [(6.0, 8.0, 0.8)]                       # removes 1.2s at 6.8
PLAN_TWO = [(6.0, 8.0, 0.8), (10.0, 12.0, 0.8)]   # removes 1.2s twice


def test_remap_before_cut_is_identity():
    assert tighten.remap(0.0, PLAN_ONE) == 0.0
    assert tighten.remap(5.0, PLAN_ONE) == 5.0
    assert tighten.remap(6.8, PLAN_ONE) == 6.8     # end of the kept air


def test_remap_inside_removed_span_collapses_to_kept_air():
    assert tighten.remap(7.0, PLAN_ONE) == 6.8
    assert tighten.remap(7.9, PLAN_ONE) == 6.8
    assert tighten.remap(8.0, PLAN_ONE) == 6.8


def test_remap_after_cut_shifts_by_removed_time():
    assert tighten.remap(9.0, PLAN_ONE) == pytest.approx(7.8)
    assert tighten.remap(12.0, PLAN_ONE) == pytest.approx(10.8)


def test_remap_multiple_gaps_accumulate():
    assert tighten.remap(9.0, PLAN_TWO) == pytest.approx(7.8)     # after gap 1
    assert tighten.remap(11.5, PLAN_TWO) == pytest.approx(9.6)    # inside gap 2
    assert tighten.remap(13.0, PLAN_TWO) == pytest.approx(10.6)   # after both


def test_remap_empty_plan_is_identity():
    assert tighten.remap(7.7, []) == 7.7


def test_remap_is_monotonic_non_decreasing():
    ts = [i * 0.1 for i in range(0, 150)]
    mapped = [tighten.remap(t, PLAN_TWO) for t in ts]
    assert all(b >= a for a, b in zip(mapped, mapped[1:]))


def test_remap_words_adjusts_timings_and_keeps_text():
    words = [W("before", 5.0, 5.5), W("after", 9.0, 9.5)]
    out = tighten.remap_words(words, PLAN_ONE)
    assert [w["text"] for w in out] == ["before", "after"]
    assert out[0]["start"] == 5.0 and out[0]["end"] == 5.5
    assert out[1]["start"] == pytest.approx(7.8)
    assert out[1]["end"] == pytest.approx(8.3)
    # Originals untouched (remap_words copies).
    assert words[1]["start"] == 9.0


def test_remap_words_empty_plan_passthrough():
    words = [W("a", 1.0, 1.4)]
    assert tighten.remap_words(words, []) == words


def test_total_removed():
    assert tighten.total_removed(PLAN_ONE) == pytest.approx(1.2)
    assert tighten.total_removed(PLAN_TWO) == pytest.approx(2.4)
    assert tighten.total_removed([]) == 0


# ----------------------------------------------------------------------
# apply_tighten
# ----------------------------------------------------------------------

def test_apply_empty_plan_copies_through(tmp_path):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"not really a video")
    out = tmp_path / "out.mp4"
    result = tighten.apply_tighten(str(src), [], str(out))
    assert result == str(out)
    assert out.read_bytes() == b"not really a video"


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg/ffprobe not available in this environment")
def test_apply_tighten_renders_shorter_clip(tmp_path):
    src = str(tmp_path / "src.mp4")
    out = str(tmp_path / "out.mp4")
    # 12s synthetic clip: colour bars + a tone.
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
         "-f", "lavfi", "-i", "sine=frequency=440", "-t", "12",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", src],
        check=True, capture_output=True)
    tighten.apply_tighten(src, PLAN_ONE, out)

    def dur(path):
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path], capture_output=True, text=True)
        return float(r.stdout.strip())

    # 12s minus the 1.2s removed, within half a second of frame-grid slack.
    assert abs(dur(out) - 10.8) < 0.5
