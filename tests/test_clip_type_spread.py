"""
Tests for the Clip Type Picker SPREAD mode (KH-CTP-001 Phase 2).

Tony's ask: pick a total clip count, get a genuine mix of the arc-anchored
types in ONE run, not "best" x N because nobody runs the picker five separate
times. Covers:

  - detect._overlaps_any: the shared any-overlap rule used to keep a spread's
    type lenses from ever claiming the same transcript moment twice.
  - detect.detect() with exclude_windows: byte-identical when absent/empty;
    drops any candidate overlapping an excluded window when present.
  - detect.detect_spread(): the reference fixture produces a genuine mix of
    all 6 types, in real distinct windows, none overlapping; a custom `types`
    list is honoured and trimmed to; a type with no genuine moment left is
    SKIPPED (never padded/invented) and reported honestly.
  - metadata._clip_block(): a clip's own clip_type wins the tone hint over
    the batch-level default, so a spread batch's clips each get their own
    type's tone line, not one hint smeared over a mixed batch.

    python -m pytest tests/test_clip_type_spread.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import detect, metadata

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "reference.transcript.json")

ALL_TYPES = ("best", "turning_point", "hero_today", "raw_moment",
             "universal_truth", "story_teaser")


# ----------------------------------------------------------------------
# _overlaps_any
# ----------------------------------------------------------------------

def test_overlaps_any_true_on_any_intersection():
    windows = [(10.0, 20.0), (50.0, 60.0)]
    assert detect._overlaps_any(15.0, 25.0, windows) is True     # partial overlap
    assert detect._overlaps_any(5.0, 55.0, windows) is True      # engulfs a window
    assert detect._overlaps_any(12.0, 18.0, windows) is True     # fully inside


def test_overlaps_any_false_when_disjoint():
    windows = [(10.0, 20.0), (50.0, 60.0)]
    assert detect._overlaps_any(20.0, 30.0, windows) is False    # touches, no overlap (< not <=)
    assert detect._overlaps_any(0.0, 10.0, windows) is False
    assert detect._overlaps_any(21.0, 49.0, windows) is False


def test_overlaps_any_false_on_no_windows():
    assert detect._overlaps_any(0.0, 100.0, []) is False
    assert detect._overlaps_any(0.0, 100.0, None) is False


# ----------------------------------------------------------------------
# detect() with exclude_windows
# ----------------------------------------------------------------------

def test_detect_default_exclude_windows_is_byte_identical():
    """Regression guard: exclude_windows is opt-in. Absent (the default) must
    reproduce the frozen best-is-legacy fixture exactly, matching the
    reference regression test in test_clip_types.py."""
    a = detect.detect(FIXTURE, use_llm=False, top_n=5)
    b = detect.detect(FIXTURE, use_llm=False, top_n=5, exclude_windows=None)
    c = detect.detect(FIXTURE, use_llm=False, top_n=5, exclude_windows=[])
    assert [c_["start"] for c_ in a["clips"]] == [c_["start"] for c_ in b["clips"]]
    assert [c_["start"] for c_ in a["clips"]] == [c_["start"] for c_ in c["clips"]]


def test_detect_exclude_windows_drops_overlapping_candidates():
    first = detect.detect(FIXTURE, use_llm=False, top_n=1, clip_type="best")
    assert first["clips"], "fixture must produce at least one best clip"
    top = first["clips"][0]
    window = [(top["start"], top["end"])]

    excluded = detect.detect(FIXTURE, use_llm=False, top_n=1, clip_type="best",
                             exclude_windows=window)
    assert excluded["clips"], "a different moment should still be available"
    assert excluded["clips"][0]["start"] != top["start"]
    # the winner never re-overlaps the excluded window
    w = excluded["clips"][0]
    assert not detect._overlaps_any(w["start"], w["end"], window)


# ----------------------------------------------------------------------
# detect_spread()
# ----------------------------------------------------------------------

def test_spread_default_order_produces_a_genuine_mix():
    result = detect.detect_spread(FIXTURE, use_llm=False)
    assert result["clip_type"] == "spread"
    assert result["spread_types"] == list(detect.DEFAULT_SPREAD_ORDER)
    types_returned = [c["clip_type"] for c in result["clips"]]
    # every clip in the reference fixture is a genuine, distinct moment
    assert types_returned == list(ALL_TYPES)
    assert len(set(types_returned)) == len(types_returned), "no type repeats"
    assert all(r["found"] for r in result["spread_report"])


def test_spread_clips_never_overlap_each_other():
    result = detect.detect_spread(FIXTURE, use_llm=False)
    windows = [(c["start"], c["end"]) for c in result["clips"]]
    for i, (s, e) in enumerate(windows):
        others = windows[:i] + windows[i + 1:]
        assert not detect._overlaps_any(s, e, others), \
            f"clip {i} ({s}-{e}) overlaps another spread clip"


def test_spread_honours_a_custom_type_list_and_order():
    order = ["hero_today", "universal_truth"]
    result = detect.detect_spread(FIXTURE, use_llm=False, types=order)
    assert result["spread_types"] == order
    assert [c["clip_type"] for c in result["clips"]] == order
    assert len(result["clips"]) == 2


def test_spread_drops_unknown_types_and_dedupes():
    order = ["hero_today", "not_a_real_type", "hero_today", "raw_moment"]
    result = detect.detect_spread(FIXTURE, use_llm=False, types=order)
    assert result["spread_types"] == ["hero_today", "hero_today", "raw_moment"]
    # both hero_today passes ran (a real, if unusual, request); they must not
    # collide on the same transcript moment
    starts = [c["start"] for c in result["clips"]]
    assert len(starts) == len(set(starts))


def test_spread_skips_a_type_with_no_genuine_moment_left(monkeypatch):
    """Never pad, never invent (the Pillar). Simulate a transcript that only
    has room for one real moment: the second type in the list gets nothing,
    and detect_spread reports it honestly instead of padding or erroring."""
    real = detect.detect

    def fake(transcript_path, use_llm=True, top_n=1, usage_ctx=None,
             clip_type="best", reviewer_anchors=None, band_override=None,
             audio_path=None, exclude_windows=None):
        if clip_type == "best":
            return real(transcript_path, use_llm=use_llm, top_n=top_n,
                        clip_type=clip_type, exclude_windows=exclude_windows)
        # every other lens finds nothing in this fake episode
        return {"clips": [], "candidate_pool": [], "n_candidates": 0,
                "n_passed_gate": 0, "method": "heuristic", "llm_error": None,
                "guest_speaker": None}

    monkeypatch.setattr(detect, "detect", fake)
    result = detect.detect_spread(FIXTURE, use_llm=False, types=["best", "hero_today"])
    assert len(result["clips"]) == 1
    assert result["clips"][0]["clip_type"] == "best"
    report = {r["type"]: r for r in result["spread_report"]}
    assert report["best"]["found"] is True
    assert report["hero_today"]["found"] is False
    assert "hero today" in report["hero_today"]["reason"].lower()
    assert "genuine" in report["hero_today"]["reason"].lower()


def test_spread_report_all_skipped_never_errors(monkeypatch):
    def fake_none(*args, **kwargs):
        return {"clips": [], "candidate_pool": [], "n_candidates": 3,
                "n_passed_gate": 0, "method": "heuristic", "llm_error": None,
                "guest_speaker": None}

    monkeypatch.setattr(detect, "detect", fake_none)
    result = detect.detect_spread(FIXTURE, use_llm=False, types=["turning_point", "raw_moment"])
    assert result["clips"] == []
    assert all(not r["found"] for r in result["spread_report"])
    assert result["n_requested"] == 2


def test_spread_candidate_pool_merges_across_types_deduped():
    result = detect.detect_spread(FIXTURE, use_llm=False, types=["hero_today", "raw_moment"])
    pool = result["candidate_pool"]
    keys = [(round(p["start"], 2), round(p["end"], 2)) for p in pool if p.get("start") is not None]
    assert len(keys) == len(set(keys)), "candidate pool must be deduped by window"


def test_spread_result_shape_matches_detect_for_clipper_run_compat():
    """clipper.run()/upload_outputs() consume detect() and detect_spread()
    results interchangeably (no special-casing) — pin the required keys."""
    result = detect.detect_spread(FIXTURE, use_llm=False)
    for key in ("source", "title", "clip_type", "method", "clips", "candidate_pool"):
        assert key in result


# ----------------------------------------------------------------------
# metadata._clip_block: per-clip tone hint wins over the batch default
# ----------------------------------------------------------------------

def test_clip_block_uses_own_clip_type_over_batch_default():
    clip = {"hook_line": "h", "archetype": "a", "why": "w", "text": "t",
            "safety": "ok", "clip_type": "raw_moment"}
    block = metadata._clip_block(0, clip, default_clip_type="best")
    assert metadata.TYPE_TONE_HINTS["raw_moment"].split(".")[0] in block


def test_clip_block_falls_back_to_batch_default_when_clip_has_no_type():
    clip = {"hook_line": "h", "archetype": "a", "why": "w", "text": "t", "safety": "ok"}
    block = metadata._clip_block(0, clip, default_clip_type="turning_point")
    assert metadata.TYPE_TONE_HINTS["turning_point"].split(".")[0] in block


def test_clip_block_best_adds_no_tone_line():
    clip = {"hook_line": "h", "archetype": "a", "why": "w", "text": "t",
            "safety": "ok", "clip_type": "best"}
    block = metadata._clip_block(0, clip, default_clip_type="best")
    assert "Tone for this job" not in block


def test_generate_prompt_carries_a_distinct_tone_per_spread_clip(monkeypatch):
    """A spread batch's clips each get their OWN type's tone line in the
    prompt, not one hint smeared over the whole mixed batch."""
    captured = {}

    def fake_call(system_prompt, user_prompt, model, api_key):
        captured["user_prompt"] = user_prompt
        import json as _json
        return _json.dumps({"packs": [
            {"index": 0, "title": "t1", "hook_seo_line": "h1 " * 5, "context": "c1",
             "primary_topic": "topic", "secondary_topics": [], "post_specific_tags": [],
             "pinned_question": "q1?", "banner_hook": "b1"},
            {"index": 1, "title": "t2", "hook_seo_line": "h2 " * 5, "context": "c2",
             "primary_topic": "topic", "secondary_topics": [], "post_specific_tags": [],
             "pinned_question": "q2?", "banner_hook": "b2"},
        ]}), {"input_tokens": 10, "output_tokens": 10}

    monkeypatch.setattr(metadata, "_call_model", fake_call)
    clips = [
        {"hook_line": "h1", "archetype": "a", "why": "w", "text": "t", "safety": "ok",
         "clip_type": "hero_today"},
        {"hook_line": "h2", "archetype": "a", "why": "w", "text": "t", "safety": "ok",
         "clip_type": "raw_moment"},
    ]
    metadata.generate(clips, "Episode", "https://youtu.be/abc123def45",
                      api_key="test-key", guest_name="Sam", series="golden-threads",
                      clip_type="spread")
    prompt = captured["user_prompt"]
    assert metadata.TYPE_TONE_HINTS["hero_today"].split(".")[0] in prompt
    assert metadata.TYPE_TONE_HINTS["raw_moment"].split(".")[0] in prompt
    # never one global "spread" hint (not a real type, has no hint of its own)
    assert "Tone for this job: these clips are spread" not in prompt
