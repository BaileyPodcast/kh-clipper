"""
Parity tests for the worker's Shorts packaging (src/packaging.py) against the
locked v5 standard (hashtags + title re-locked 2026-08). These pin the SAME
deterministic scaffolding the app pins in kh-studio
tests/shorts-packaging.test.ts, so the two repos cannot drift: the blurbs, the
About-KH block, the 3-hashtag locked build, the hook-only title and the tags
rules must match on both sides.

    python -m pytest tests/test_packaging.py
    python tests/test_packaging.py        # also runs standalone (no pytest)
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import packaging

BANNED = re.compile(r"\b(journey|inspiring|amazing|powerful|resilient)\b", re.I)
EM_OR_EN_DASH = re.compile(r"[—–]")

# The exact strings the app pins (kh-studio shorts-packaging.ts / its test). Kept
# here verbatim so a change on either side that breaks parity fails a test.
ABOUT_KH_EXPECTED = (
    "Kintsugi Heroes is a not-for-profit Australian podcast network sharing real "
    "stories of resilience, transformation, and lived experience. Inspired by the "
    "Japanese art of kintsugi, repairing broken pottery with gold, we believe our "
    "cracks are what make us stronger."
)

# Golden values (locked cross-repo contract): EXACTLY 3 hashtags.
HASHTAGS_GOLDEN_THREADS_EXPECTED = ["#Shorts", "#GoldenThreads", "#KintsugiHeroes"]
HASHTAGS_MAIN_FEED_EXPECTED = ["#Shorts", "#KintsugiHeroes", "#KintsugiHeroesPodcast"]


def test_series_map_is_the_8_worker_slugs():
    assert sorted(packaging.SERIES.keys()) == sorted([
        "alpine-series", "animals-and-us", "australian-carers", "connecting-seniors",
        "golden-threads", "grit-diaries", "kintsugi-heroes", "river-murray-recovery-stories",
    ])


def test_about_kh_matches_the_app_and_is_house_voice_clean():
    assert packaging.ABOUT_KH == ABOUT_KH_EXPECTED
    assert not EM_OR_EN_DASH.search(packaging.ABOUT_KH)
    assert not BANNED.search(packaging.ABOUT_KH)


def test_blurbs_are_house_voice_clean():
    for slug, meta in packaging.SERIES.items():
        assert not EM_OR_EN_DASH.search(meta["blurb"]), slug
        assert not BANNED.search(meta["blurb"]), slug
        assert meta["tag"].startswith("#")


def test_hashtags_locked_build_matches_the_app():
    # Topic args are accepted for compatibility but never change the block.
    tags = packaging.compose_hashtags(
        "golden-threads", "disability",
        secondary_topics=["resilience", "healing", "vulnerability"])
    assert tags == HASHTAGS_GOLDEN_THREADS_EXPECTED


def test_hashtags_main_feed_dedupes_and_backfills_to_exactly_3():
    tags = packaging.compose_hashtags("kintsugi-heroes", "grief")
    assert tags == HASHTAGS_MAIN_FEED_EXPECTED
    assert len(tags) == 3
    assert len(set(t.lower() for t in tags)) == 3
    assert [t.lower() for t in tags].count("#kintsugiheroes") == 1


def test_hashtags_are_exactly_3_for_every_series():
    for slug in packaging.SERIES:
        tags = packaging.compose_hashtags(slug)
        assert len(tags) == 3, slug
        assert tags[0] == "#Shorts", slug
        assert len(set(t.lower() for t in tags)) == 3, slug


def test_topic_casing_normalised():
    assert packaging.canonical_topic_tag("ptsd") == "#PTSD"
    assert packaging.canonical_topic_tag("#adhd") == "#ADHD"


def test_title_is_hook_only_no_suffix_no_hero():
    # The hook ONLY: no brand suffix, and a hero name is never appended.
    assert packaging.compose_title("Three years sober", "Sam Lee") == "Three years sober"
    assert packaging.compose_title("The moment everything changed") == \
        "The moment everything changed"


def test_title_over_60_trims_at_a_word_boundary_no_ellipsis():
    long_hook = "A very long honest hook line that eats most of the character budget here"
    t = packaging.compose_title(long_hook, "Alexandra Featherstone-Montgomery")
    # Golden trimmed value: "character" ends exactly on the 60-char cap, so the
    # trim keeps it whole and drops the words after it.
    assert t == "A very long honest hook line that eats most of the character"
    assert len(t) <= packaging.TITLE_MAX
    assert not t.endswith("...") and "…" not in t
    assert "Featherstone" not in t and "|" not in t


def test_title_at_exactly_60_is_untouched():
    hook = "x" * 60
    assert packaging.compose_title(hook) == hook


def test_title_trim_backs_up_off_a_mid_word_cut():
    # "characters" would need 61 chars to survive whole, so the trim drops it.
    hook = "A very long honest hook line that eats most of the characters budget"
    assert packaging.compose_title(hook) == \
        "A very long honest hook line that eats most of the"


def test_description_is_7_part_and_clean():
    d = packaging.compose_description(
        "grit-diaries",
        "Three years sober when it nearly ended her, a Grit Diaries recovery story.",
        "She had rebuilt everything. Then it nearly came undone.",
        ["#Shorts", "#ytshorts", "#recovery"],
        full_episode_url="https://youtu.be/abc123")
    assert "About Kintsugi Heroes:" in d
    assert packaging.ABOUT_KH in d
    assert "About Grit Diaries:" in d
    assert packaging.SERIES["grit-diaries"]["blurb"] in d
    assert "Subscribe for new episodes fortnightly" in d
    assert d.strip().endswith("#Shorts #ytshorts #recovery")
    assert not EM_OR_EN_DASH.search(d)


def test_tags_field_within_500_keeps_hero_and_locked_broad():
    long_post = [f"very specific clip keyword number {i}" for i in range(12)]
    res = packaging.compose_tags(
        "grit-diaries", hero_name="Someone With A Genuinely Long Name",
        primary_topic="recovery", post_specific=long_post)
    weight = sum(len(t) + (2 if any(c.isspace() for c in t) else 0) for t in res["flat"])
    assert weight <= 500
    assert res["groups"]["post_specific"][0] == "Someone With A Genuinely Long Name"
    for locked in packaging.BROAD_TAGS_LOCKED:
        assert locked in res["flat"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
