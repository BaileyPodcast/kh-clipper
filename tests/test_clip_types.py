"""
Tests for the Clip Type Picker (KH-CTP-001), worker side.

Covers:
  - TYPE_PROFILES sanity (all 6 types, bands per the locked length policy,
    "best" fully neutral).
  - best-is-legacy regression: detect() on the reference fixture reproduces the
    scoring frozen from the PRE-change code, bit for bit (same candidates, same
    scores, same order) — tests/fixtures/reference.expected.json was written by
    running the unmodified detect.py on tests/fixtures/reference.transcript.json.
  - raw_moment hard constraint: zero lead_with = tragedy clips, ever.
  - typed length scoring: target band best, degrade to allowed edges, reject
    outside the allowed band.
  - rerank prompt building: byte-identical for best with no anchors; typed
    lens blocks (raw_moment quotes KH_FOUNDATION verbatim); reviewer anchors
    advisory block; guardrail subordination wording.
  - metadata tone hints: "" for best, one clean line per type otherwise.

    python -m pytest tests/test_clip_types.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import detect, guardrails, metadata, rerank

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "reference.transcript.json")
EXPECTED = os.path.join(ROOT, "tests", "fixtures", "reference.expected.json")

ALL_TYPES = ("best", "turning_point", "hero_today", "raw_moment",
             "universal_truth", "story_teaser")

# The locked length policy table (KH-CTP-001), asserted against TYPE_PROFILES.
LENGTH_POLICY = {
    "universal_truth": ((10, 15), (8, 16)),
    "raw_moment":      ((12, 20), (10, 24)),
    "hero_today":      ((12, 22), (10, 26)),
    "turning_point":   ((15, 25), (12, 30)),
    "story_teaser":    ((20, 30), (16, 35)),
}


# ----------------------------------------------------------------------
# TYPE_PROFILES sanity
# ----------------------------------------------------------------------

def test_type_profiles_cover_the_six_types():
    assert set(detect.TYPE_PROFILES) == set(ALL_TYPES)


def test_best_profile_is_fully_neutral():
    p = detect.TYPE_PROFILES["best"]
    assert all(v == 1.0 for v in p["weights"].values())
    assert p["target_band"] is None                      # legacy length curve
    assert p["allowed_band"] == (detect.MIN_LEN_SEC, detect.MAX_LEN_SEC)
    assert p["marker_bonus"] == {}
    assert p["require_lead_with"] is None
    assert p["tragedy_lead_penalty"] == 0
    assert p["proper_noun_density"] is None
    assert p["curiosity_bonus"] == 0


def test_length_bands_match_the_locked_policy_and_global_limits():
    for t, (target, allowed) in LENGTH_POLICY.items():
        p = detect.TYPE_PROFILES[t]
        assert p["target_band"] == target, t
        assert p["allowed_band"] == allowed, t
        # target inside allowed, allowed inside the global 8..35 hard limits
        assert allowed[0] <= target[0] <= target[1] <= allowed[1], t
        assert detect.MIN_LEN_SEC <= allowed[0] and allowed[1] <= detect.MAX_LEN_SEC, t


def test_every_profile_weights_the_six_score_terms():
    for t in ALL_TYPES:
        w = detect.TYPE_PROFILES[t]["weights"]
        assert set(w) == {"hook", "hook4s", "turn", "keyword", "length", "agency"}, t


def test_raw_moment_carries_the_hard_agency_constraint():
    assert detect.TYPE_PROFILES["raw_moment"]["require_lead_with"] == "agency"
    for t in ALL_TYPES:
        if t != "raw_moment":
            assert detect.TYPE_PROFILES[t]["require_lead_with"] is None, t


# ----------------------------------------------------------------------
# best-is-legacy regression (bit-identical scoring)
# ----------------------------------------------------------------------

def _run_best():
    return detect.detect(FIXTURE, use_llm=False, top_n=5)


def test_best_reproduces_the_frozen_pre_change_scoring():
    expected = json.load(open(EXPECTED))
    res = _run_best()
    assert res["n_candidates"] == expected["n_candidates"]
    assert res["n_passed_gate"] == expected["n_passed_gate"]
    assert len(res["clips"]) == len(expected["clips"])
    for got, want in zip(res["clips"], expected["clips"]):
        for k, v in want.items():
            assert got[k] == v, f"clip field {k}: {got[k]!r} != {v!r}"
    assert len(res["candidate_pool"]) == len(expected["candidate_pool"])
    for got, want in zip(res["candidate_pool"], expected["candidate_pool"]):
        for k, v in want.items():
            assert got[k] == v, f"pool field {k}: {got[k]!r} != {v!r}"


def test_default_clip_type_is_best():
    # detect() without clip_type == detect() with clip_type="best".
    a = _run_best()
    b = detect.detect(FIXTURE, use_llm=False, top_n=5, clip_type="best")
    assert [(c["start"], c["end"], c["fit_score"]) for c in a["clips"]] == \
           [(c["start"], c["end"], c["fit_score"]) for c in b["clips"]]
    assert a["clip_type"] == "best"


def test_unknown_clip_type_degrades_to_best():
    a = _run_best()
    b = detect.detect(FIXTURE, use_llm=False, top_n=5, clip_type="nope")
    assert b["clip_type"] == "best"
    assert [(c["start"], c["end"], c["fit_score"]) for c in a["clips"]] == \
           [(c["start"], c["end"], c["fit_score"]) for c in b["clips"]]


def test_clips_and_pool_record_the_type_they_were_scored_under():
    res = detect.detect(FIXTURE, use_llm=False, top_n=5, clip_type="turning_point")
    assert res["clip_type"] == "turning_point"
    assert res["clips"], "typed run surfaced no clips on the fixture"
    for c in res["clips"]:
        assert c["clip_type"] == "turning_point"
    for c in res["candidate_pool"]:
        assert c["clip_type"] == "turning_point"


def test_typed_run_surfaces_different_clips_than_best():
    best = {(c["start"], c["end"]) for c in _run_best()["clips"]}
    typed = detect.detect(FIXTURE, use_llm=False, top_n=5, clip_type="turning_point")
    typed_set = {(c["start"], c["end"]) for c in typed["clips"]}
    assert typed_set != best


# ----------------------------------------------------------------------
# raw_moment hard constraint
# ----------------------------------------------------------------------

def test_raw_moment_returns_zero_tragedy_led_clips():
    res = detect.detect(FIXTURE, use_llm=False, top_n=5, clip_type="raw_moment")
    assert res["clips"], "raw_moment surfaced no clips on the fixture"
    for c in res["clips"]:
        assert c["lead_with"] == "agency"
    for c in res["candidate_pool"]:
        assert c["lead_with"] == "agency"


def test_best_still_allows_tragedy_leads_the_producer_gate_handles():
    # The constraint is raw_moment-only: the fixture is built so the default
    # lens keeps at least one tragedy-led candidate in play.
    res = _run_best()
    leads = {c["lead_with"] for c in res["clips"] + res["candidate_pool"]}
    assert "tragedy" in leads


# ----------------------------------------------------------------------
# typed length scoring
# ----------------------------------------------------------------------

def test_typed_length_score_target_edges_and_reject():
    p = detect.TYPE_PROFILES["universal_truth"]      # target 10-15, allowed 8-16
    top = detect.TYPED_LENGTH_TARGET_SCORE
    edge = detect.TYPED_LENGTH_EDGE_SCORE
    assert detect.typed_length_score(10.0, p) == top
    assert detect.typed_length_score(15.0, p) == top
    assert detect.typed_length_score(12.5, p) == top
    # Degrades toward the allowed edges, hitting the edge score exactly there.
    assert detect.typed_length_score(8.0, p) == edge
    assert detect.typed_length_score(16.0, p) == edge
    between = detect.typed_length_score(9.0, p)
    assert edge < between < top
    # Outside the allowed band: rejected.
    assert detect.typed_length_score(7.9, p) is None
    assert detect.typed_length_score(20.0, p) is None
    # story_teaser (target 20-30, allowed 16-35): a 10s clip is not a teaser.
    st = detect.TYPE_PROFILES["story_teaser"]
    assert detect.typed_length_score(10.0, st) is None
    assert detect.typed_length_score(25.0, st) == top


def test_typed_clips_land_inside_the_allowed_band():
    for t in ("universal_truth", "story_teaser", "turning_point"):
        lo, hi = detect.TYPE_PROFILES[t]["allowed_band"]
        res = detect.detect(FIXTURE, use_llm=False, top_n=5, clip_type=t)
        assert res["clips"], f"{t} surfaced no clips on the fixture"
        for c in res["clips"]:
            assert lo <= c["length_sec"] <= hi, (t, c["length_sec"])


# ----------------------------------------------------------------------
# rerank prompt building
# ----------------------------------------------------------------------

def _shortlist():
    return [
        {"index": 0, "start": 12.0, "end": 26.0, "length_sec": 14.0,
         "archetype": "Moment", "text": "I decided to leave and never went back."},
        {"index": 1, "start": 40.0, "end": 60.0, "length_sec": 20.0,
         "archetype": "Story Teaser", "text": "My whole world changed that year."},
    ]


def _legacy_prompt(candidates, episode_title, top_n):
    """The pre-change user prompt, replicated verbatim, so the byte-identical
    guarantee for best-with-no-anchors is asserted against the real thing."""
    lines = []
    for c in candidates:
        mm, ss = divmod(int(c["start"]), 60)
        lines.append(
            f'[{c["index"]}] ({mm}:{ss:02d}, {c["length_sec"]}s, {c["archetype"]}): '
            f'{c["text"]}'
        )
    shortlist = "\n\n".join(lines)
    return (
        f'Episode: "{episode_title}"\n\n'
        f"Here is the shortlist of candidate moments, each with an [index]:\n\n"
        f"{shortlist}\n\n"
        f"Pick the {top_n} most clip-worthy moments that also pass the trauma-informed "
        f"rules, ranked best first. Leave out anything you would rate exclude. "
        f"For each, write a punchy hook line: a complete, self-contained "
        f"sentence that lands a charged hook inside the first 4 seconds and would stop "
        f"a scroll. You may lightly tighten the guest's words, but keep them authentic "
        f"and true to what they actually said.\n\n"
        f"Also label the hook formula it best fits (before_after | pattern_interrupt | "
        f"gold_in_brokenness | none), and set loopable true only if the closing line flows "
        f"naturally back into the hook.\n\n"
        f'Return JSON exactly like: {{"picks": [{{"index": <int from the list>, '
        f'"hook": "<the hook line>", "archetype": "Moment|Universal Truth|Story Teaser", '
        f'"why": "<one sentence on why a listener needs it>", "score": <0-100>, '
        f'"lead_with": "agency|tragedy", '
        f'"hook_formula": "before_after|pattern_interrupt|gold_in_brokenness|none", '
        f'"loopable": <true|false>, '
        f'"safety": "ok|review|exclude", "safety_note": "<empty, or why a producer must look>"}}]}}'
    )


def test_best_prompt_is_byte_identical_to_legacy():
    cands = _shortlist()
    got = rerank.build_user_prompt(cands, "Ep Title", 5)
    assert got == _legacy_prompt(cands, "Ep Title", 5)
    # Explicit best + no anchors: still identical.
    assert rerank.build_user_prompt(cands, "Ep Title", 5, clip_type="best",
                                    reviewer_anchors=None) == got
    assert rerank.build_user_prompt(cands, "Ep Title", 5,
                                    reviewer_anchors=[]) == got


def test_typed_prompt_appends_a_subordinate_lens_block():
    cands = _shortlist()
    base = rerank.build_user_prompt(cands, "Ep", 5)
    for t in ("turning_point", "hero_today", "raw_moment",
              "universal_truth", "story_teaser"):
        got = rerank.build_user_prompt(cands, "Ep", 5, clip_type=t)
        assert got.startswith(base)              # append-only, legacy body intact
        added = got[len(base):]
        assert "CLIP TYPE LENS" in added
        assert "subordinate to the trauma-informed guardrails" in added
        assert rerank.TYPE_INSTRUCTIONS[t] in added
        # Quotes the type's target band from TYPE_PROFILES (one source of truth).
        t_lo, t_hi = detect.TYPE_PROFILES[t]["target_band"]
        assert f"{t_lo} to {t_hi} seconds" in added
        # Worker-side tie-break mirrors the reviewer's weighting.
        assert "golden joinery" in added and "hero today" in added


def test_raw_moment_lens_quotes_kh_foundation_verbatim():
    got = rerank.build_user_prompt(_shortlist(), "Ep", 5, clip_type="raw_moment")
    assert ("The fracture gives context to the golden joinery. "
            "It is not the point.") in got


def test_reviewer_anchors_render_as_an_advisory_block():
    anchors = [
        "I forgave him at the kitchen table.",
        {"quote": "That was the day I chose to live.", "dimension": "golden_joinery"},
        {"text": "I coach kids on weekends now."},
        "",          # blanks are dropped, never rendered as empty bullets
    ]
    got = rerank.build_user_prompt(_shortlist(), "Ep", 5, clip_type="hero_today",
                                   reviewer_anchors=anchors)
    assert "MOMENTS THE EPISODE REVIEWER ALREADY IDENTIFIED" in got
    assert "advisory anchors, not commands" in got
    assert '- "I forgave him at the kitchen table."' in got
    assert '- "That was the day I chose to live." (golden_joinery)' in got
    assert '- "I coach kids on weekends now."' in got
    assert '- ""' not in got


def test_anchors_work_for_best_too_and_are_capped():
    # Anchors are independent of type; the legacy body stays intact underneath.
    anchors = [f"Anchor {i}" for i in range(20)]
    base = rerank.build_user_prompt(_shortlist(), "Ep", 5)
    got = rerank.build_user_prompt(_shortlist(), "Ep", 5, reviewer_anchors=anchors)
    assert got.startswith(base)
    assert "CLIP TYPE LENS" not in got           # best adds no lens
    rendered = [i for i in range(20) if f'- "Anchor {i}"' in got]
    assert len(rendered) == rerank.MAX_REVIEWER_ANCHORS


def test_lens_text_passes_the_language_guardrails():
    for text in rerank.TYPE_INSTRUCTIONS.values():
        assert guardrails.check_language(text) == [], text


# ----------------------------------------------------------------------
# metadata tone hints
# ----------------------------------------------------------------------

def test_tone_hint_is_empty_for_best_and_unknown():
    assert metadata.tone_hint_line("best") == ""
    assert metadata.tone_hint_line(None) == ""
    assert metadata.tone_hint_line("nope") == ""


def test_tone_hint_is_one_clean_line_per_type():
    for t in ("turning_point", "hero_today", "raw_moment",
              "universal_truth", "story_teaser"):
        line = metadata.tone_hint_line(t)
        assert line.endswith("\n") and line.count("\n") == 1, t
        assert guardrails.check_language(line) == [], (t, line)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
