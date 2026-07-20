"""
Parity tests for the worker's Shorts packaging (src/packaging.py) against the
locked v5 standard. These pin the SAME deterministic scaffolding the app pins in
kh-studio tests/shorts-packaging.test.ts, so the two repos cannot drift: the
blurbs, the About-KH block, the 15-hashtag locked order and the tags rules must
match on both sides.

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

HASHTAGS_GOLDEN_THREADS_EXPECTED = [
    "#Shorts", "#ytshorts", "#disability", "#viralshorts", "#shortsvideo",
    "#shortsfeed", "#KintsugiHeroes", "#GoldenThreads", "#KintsugiHeroesPodcast",
    "#resilience", "#healing", "#vulnerability", "#realstories", "#livedexperience",
    "#australianpodcast",
]


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


def test_hashtags_locked_order_matches_the_app():
    tags = packaging.compose_hashtags(
        "golden-threads", "disability",
        secondary_topics=["resilience", "healing", "vulnerability"])
    assert tags == HASHTAGS_GOLDEN_THREADS_EXPECTED


def test_hashtags_reach_15_and_dedupe_on_main_feed():
    tags = packaging.compose_hashtags("kintsugi-heroes", "grief")
    assert len(tags) == 15
    assert len(set(t.lower() for t in tags)) == 15
    assert [t.lower() for t in tags].count("#kintsugiheroes") == 1


def test_topic_casing_normalised():
    assert packaging.canonical_topic_tag("ptsd") == "#PTSD"
    assert packaging.canonical_topic_tag("#adhd") == "#ADHD"


def test_title_convention_and_cap():
    assert packaging.compose_title("Three years sober", "Sam Lee") == \
        "Three years sober | Sam Lee | Kintsugi Heroes Shorts"
    assert packaging.compose_title("The moment everything changed") == \
        "The moment everything changed | Kintsugi Heroes Shorts"
    long_hook = "A very long honest hook line that eats most of the character budget here"
    t = packaging.compose_title(long_hook, "Alexandra Featherstone-Montgomery")
    assert len(t) <= 100
    assert t.endswith(" | Kintsugi Heroes Shorts")
    assert "Featherstone" not in t


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
