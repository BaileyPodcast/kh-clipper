"""
Interrupt Theory upgrades to the rerank judgment pass (src/rerank.py): the
subject+stakes 4-second check, the 4 S's, the lock-in zone and the
bait-and-switch penalty are in the SYSTEM prompt, and the JSON response
schema is unchanged. Pure string assertions, no network.

    python -m pytest tests/test_rerank_prompt.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import rerank


def test_hook_must_answer_subject_and_stakes_in_4_seconds():
    sp = rerank.SYSTEM_PROMPT
    assert "answer TWO questions inside those first 4 seconds" in sp
    assert "SUBJECT" in sp and "STAKES" in sp


def test_the_4_ss_are_spelled_out():
    sp = rerank.SYSTEM_PROMPT
    for s in ("SUBJECT", "STAKES", "SPEED", "SUPER CLEAR"):
        assert s in sp
    assert "maximum compression" in sp
    assert "interpretable one way and one way only" in sp


def test_lock_in_zone_and_bait_and_switch():
    sp = rerank.SYSTEM_PROMPT
    assert "THE LOCK-IN ZONE" in sp
    assert "CONFIRM the hook's claim and stay on its subject" in sp
    assert "inside seconds ~4-12" in sp
    assert "bait-and-switch" in sp


def test_json_schema_unchanged():
    # The response contract must not grow fields: the additions fold into the
    # existing 0-100 score.
    prompt = rerank.build_user_prompt(
        [{"index": 0, "start": 10, "end": 25, "length_sec": 15,
          "archetype": "Moment", "text": "the line"}],
        "Episode", 3)
    for field in ('"hook"', '"archetype"', '"why"', '"score"', '"lead_with"',
                  '"hook_formula"', '"loopable"', '"safety"', '"safety_note"'):
        assert field in prompt
    assert '"subject"' not in prompt and '"stakes"' not in prompt


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
