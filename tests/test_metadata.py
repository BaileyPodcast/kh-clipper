"""
Brief 3: the Shorts copy pack is now written by Claude, but packaging.py still
assembles the locked v5 scaffolding. These tests pin the contract WITHOUT calling
the API: `metadata._call_model` is monkeypatched to return a canned response, so
the by-index merge + assembly + language gate all run offline.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import metadata, guardrails  # noqa: E402

# The 8 creative slots Claude must return per clip (metadata.py SYSTEM_PROMPT).
CREATIVE_KEYS = ("title", "hook_seo_line", "context", "primary_topic",
                 "secondary_topics", "post_specific_tags", "pinned_question", "banner_hook")


def _clean_pack(index=0):
    return {
        "index": index,
        "title": "The moment he stopped hiding",
        "hook_seo_line": "A father finds his way back after addiction, on Kintsugi Heroes.",
        "context": "He describes the turning point. A quiet decision that changed everything.",
        "primary_topic": "recovery",
        "secondary_topics": ["healing", "resilience"],
        "post_specific_tags": ["father", "recovery", "turning point"],
        "pinned_question": "Who helped you find your way back?",
        "banner_hook": "He stopped hiding",
    }


def _fake_model(packs):
    def _call(system_prompt, user_prompt, model, api_key):
        return json.dumps({"packs": packs}), {"input_tokens": 100, "output_tokens": 50}
    return _call


def _clip():
    return {"hook_line": "I stopped hiding", "archetype": "Moment", "why": "a real turn",
            "text": "the transcript line", "safety": "ok"}


def test_pack_has_eight_creative_keys_with_correct_types():
    pack = _clean_pack()
    for k in CREATIVE_KEYS:
        assert k in pack, f"missing creative slot {k}"
    assert isinstance(pack["title"], str)
    assert isinstance(pack["hook_seo_line"], str)
    assert isinstance(pack["banner_hook"], str)
    assert isinstance(pack["secondary_topics"], list)
    assert isinstance(pack["post_specific_tags"], list)


def test_generate_assembles_metadata_from_claude_pack(monkeypatch):
    monkeypatch.setattr(metadata, "_call_model", _fake_model([_clean_pack(0)]))
    clips = [_clip()]
    out = metadata.generate(clips, "Episode Title", "https://youtu.be/abc123def45",
                            api_key="test-key", guest_name="Sam", series="golden-threads")
    meta = out[0]["metadata"]
    # The 6 assembled v5 fields, correct types.
    for k in ("title", "description", "hashtags", "tags", "pinned_comment", "banner_hook"):
        assert k in meta, f"missing assembled field {k}"
    assert isinstance(meta["title"], str) and meta["title"]
    assert isinstance(meta["description"], str) and meta["description"]
    assert isinstance(meta["hashtags"], list) and meta["hashtags"]
    assert isinstance(meta["tags"], list) and meta["tags"]
    assert isinstance(meta["pinned_comment"], str) and meta["pinned_comment"]
    assert isinstance(meta["banner_hook"], str) and meta["banner_hook"]
    # A clean pack trips no language warnings.
    assert "_warnings" not in meta


def test_language_gate_flags_banned_word_and_em_dash():
    # The values rules apply to the creative slots the producer sees. A clean line
    # passes; a banned word or an em dash is flagged.
    assert guardrails.check_language("The moment he stopped hiding") == []
    assert guardrails.check_language("An inspiring story") != []      # banned word
    assert guardrails.check_language("He stopped hiding — for good") != []  # em dash


def test_generate_raises_when_no_packs(monkeypatch):
    monkeypatch.setattr(metadata, "_call_model", _fake_model([]))
    try:
        metadata.generate([_clip()], "T", "https://youtu.be/abc123def45",
                          api_key="test-key", series="golden-threads")
        assert False, "expected a raise on empty packs"
    except ValueError:
        pass
