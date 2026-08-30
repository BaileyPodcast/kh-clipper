"""
Episode QC, transcript side (src/qc_transcript.py): every function here is pure,
so these tests need no ffmpeg, no network and no Modal.

The no_go_hits tests carry the most weight. A no-go term left in a final cut is
a consent breach, and a matcher that false-positives on every long word trains
people to ignore the one flag that must never be ignored. The "Pip" inside
"epiphanies" case below is a real regression from kh-studio (2026-08-13), not a
hypothetical.

    python -m pytest tests/test_qc_transcript.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import qc_transcript as qt  # noqa: E402


def _words(spec, step=0.5):
    """[(text)] or a sentence into ClipperTranscript words: {text, start, end}."""
    tokens = spec.split() if isinstance(spec, str) else list(spec)
    out, t = [], 0.0
    for token in tokens:
        out.append({"text": token, "start": round(t, 3), "end": round(t + 0.3, 3),
                    "speaker": 0})
        t += step
    return out


# ----------------------------------------------------------------------
# no_go_hits: whole word, never substring.
# ----------------------------------------------------------------------
def test_term_does_not_match_inside_a_longer_word():
    # The 2026-08-13 regression: substring matching flagged a hero's own
    # verbatim quote because "Pip" sits inside "epiphanies".
    words = _words("I had one of those epiphanies about pipelines and discipline")
    assert qt.no_go_hits(words, ["pip"]) == []


def test_term_matches_the_whole_word_it_really_is():
    words = _words("My brother Pip was there that night")
    hits = qt.no_go_hits(words, ["pip"])
    assert len(hits) == 1
    assert hits[0]["term"] == "pip"
    assert hits[0]["start"] == 1.0          # third word, 0.5s steps
    assert hits[0]["end"] == 1.3


def test_match_survives_trailing_punctuation():
    words = _words("It was Pip, and then everything changed")
    assert len(qt.no_go_hits(words, ["pip"])) == 1


def test_multi_word_phrase_matches_across_consecutive_words_with_real_timestamps():
    words = _words("we never talk about the car accident that summer")
    hits = qt.no_go_hits(words, ["the car accident"])
    assert len(hits) == 1
    hit = hits[0]
    assert hit["term"] == "the car accident"
    assert hit["start"] == 2.0              # "the", the fifth word
    assert hit["end"] == 3.3                # "accident", the seventh word
    assert "car accident" in hit["text"]


def test_multi_word_phrase_does_not_match_when_the_words_are_apart():
    words = _words("the day of the crash there was an accident report filed")
    assert qt.no_go_hits(words, ["the car accident"]) == []


def test_matching_is_case_insensitive():
    words = _words("Then ADELAIDE happened and we moved")
    assert len(qt.no_go_hits(words, ["adelaide"])) == 1


def test_empty_terms_yield_nothing():
    words = _words("anything at all could be said here")
    assert qt.no_go_hits(words, []) == []
    assert qt.no_go_hits(words, None) == []
    assert qt.no_go_hits(words, ["", "   "]) == []


def test_empty_words_yield_nothing():
    assert qt.no_go_hits([], ["pip"]) == []
    assert qt.no_go_hits(None, ["pip"]) == []


def test_regex_metacharacters_in_a_term_are_escaped_not_executed():
    words = _words("we discussed axb and other things")
    # "a.b" must be a literal, not a wildcard that matches "axb".
    assert qt.no_go_hits(words, ["a.b"]) == []
    # And a term full of metacharacters must not raise.
    assert qt.no_go_hits(words, ["(who?) [x]* +"]) == []


def test_hits_come_back_in_time_order():
    words = _words("Pip was there and later Adelaide was mentioned")
    hits = qt.no_go_hits(words, ["adelaide", "pip"])
    assert [h["term"] for h in hits] == ["pip", "adelaide"]


def test_every_occurrence_of_a_term_is_reported():
    words = _words("Pip called and then Pip left again")
    assert len(qt.no_go_hits(words, ["pip"])) == 2


# ----------------------------------------------------------------------
# mid_word_cuts
# ----------------------------------------------------------------------
def test_cut_inside_a_word_is_flagged():
    words = [{"text": "remember", "start": 10.0, "end": 10.9}]
    cuts = qt.mid_word_cuts(words, [10.5])
    assert len(cuts) == 1
    assert cuts[0]["word"] == "remember"
    assert cuts[0]["time"] == 10.5


def test_cut_in_the_gap_between_words_is_clean():
    words = [{"text": "one", "start": 10.0, "end": 10.4},
             {"text": "two", "start": 11.0, "end": 11.4}]
    assert qt.mid_word_cuts(words, [10.7]) == []


def test_cut_within_tolerance_of_a_word_edge_is_clean():
    words = [{"text": "remember", "start": 10.0, "end": 10.9}]
    assert qt.mid_word_cuts(words, [10.05]) == []      # inside the 0.12s tolerance
    assert qt.mid_word_cuts(words, [10.85]) == []
    assert len(qt.mid_word_cuts(words, [10.05], tolerance=0.0)) == 1


def test_no_cut_points_means_no_findings():
    assert qt.mid_word_cuts(_words("a few words here"), []) == []
    assert qt.mid_word_cuts(_words("a few words here"), None) == []


# ----------------------------------------------------------------------
# duplicate_segments
# ----------------------------------------------------------------------
def test_repeated_phrase_inside_the_window_is_flagged():
    line = "and then I finally rang my sister"
    words = _words(f"{line} but nothing happened {line}")
    dups = qt.duplicate_segments(words, min_phrase_words=6)
    assert len(dups) == 1
    assert dups[0]["second_start"] > dups[0]["first_start"]


def test_repeat_outside_the_window_is_not_flagged():
    line = "and then I finally rang my sister"
    words = _words(f"{line} but nothing happened {line}", step=20.0)
    assert qt.duplicate_segments(words, window_sec=90, min_phrase_words=6) == []


def test_short_repeats_are_left_alone():
    words = _words("I said it and I said it")
    assert qt.duplicate_segments(words, min_phrase_words=6) == []


def test_punctuation_and_case_do_not_hide_a_duplicate():
    words = _words("and then I finally rang her. And then I finally rang her")
    assert len(qt.duplicate_segments(words, min_phrase_words=6)) == 1


def test_empty_transcript_yields_no_duplicates():
    assert qt.duplicate_segments([]) == []


# ----------------------------------------------------------------------
# segment_order: a keyword heuristic that reports itself as one.
# ----------------------------------------------------------------------
def _utterances(texts, speakers=None):
    out, t = [], 0.0
    for i, text in enumerate(texts):
        speaker = speakers[i] if speakers else ("A" if i % 2 == 0 else "B")
        out.append({"speaker": speaker, "text": text, "start": t, "end": t + 9.0})
        t += 10.0
    return out


def _full_shape():
    body = [f"So tell me about that time, part {i}" for i in range(12)]
    return _utterances(
        ["The day everything changed, I was sitting in the car."] +
        ["Welcome to Kintsugi Heroes, the podcast about what people rebuild."] +
        ["A content warning before we start: this episode discusses grief."] +
        body +
        ["Thanks for listening, and we will see you next episode."])


def test_full_kh_shape_reports_nothing_missing():
    result = qt.segment_order(_full_shape())
    assert result["missing"] == []
    assert result["out_of_order"] == []
    assert result["found"]["branded_intro"] == 1
    assert result["found"]["content_advisory"] == 2


def test_missing_advisory_is_reported():
    turns = [u for u in _full_shape() if "content warning" not in u["text"]]
    result = qt.segment_order(turns)
    assert "content_advisory" in result["missing"]
    assert "branded_intro" not in result["missing"]


def test_out_of_order_intro_is_reported():
    turns = _full_shape()
    turns[1], turns[2] = turns[2], turns[1]     # advisory before the branded intro
    result = qt.segment_order(turns)
    assert result["out_of_order"]


def test_segment_order_is_honest_about_being_a_heuristic():
    result = qt.segment_order(_full_shape())
    assert result["confidence"] == 0.5
    assert result["heuristic"] is True


def test_no_utterances_means_every_stage_is_unknown():
    result = qt.segment_order([])
    assert result["missing"] == qt.EPISODE_SHAPE
    assert result["turns"] == 0


# ----------------------------------------------------------------------
# transcript_fits_media: the exact tolerance worker/app.py already uses.
# ----------------------------------------------------------------------
def test_transcript_that_fits_the_media_passes():
    words = [{"text": "end", "start": 3500.0, "end": 3600.0}]
    assert qt.transcript_fits_media(words, 3611.0) is True


def test_transcript_longer_than_the_media_fails():
    words = [{"text": "end", "start": 4000.0, "end": 4200.0}]
    assert qt.transcript_fits_media(words, 3611.0) is False


def test_media_far_longer_than_the_transcript_fails():
    words = [{"text": "end", "start": 100.0, "end": 120.0}]
    assert qt.transcript_fits_media(words, 3611.0) is False


def test_unknown_media_duration_trusts_the_transcript():
    # Mirrors _prepare_supplied_transcript's `if dur and not (...)`: with nothing
    # to contradict it, provenance is trusted and the worker reports the unknown
    # duration as its own finding.
    words = [{"text": "end", "start": 10.0, "end": 20.0}]
    assert qt.transcript_fits_media(words, None) is True
    assert qt.transcript_fits_media(words, 0) is True


def test_empty_transcript_never_fits():
    assert qt.transcript_fits_media([], 3611.0) is False
    assert qt.transcript_fits_media([{"text": "x", "start": 0, "end": 0}], 3611.0) is False


# ----------------------------------------------------------------------
# speaker_count: reads utterances, never words[].speaker.
# ----------------------------------------------------------------------
def test_speaker_count_counts_distinct_codes():
    assert qt.speaker_count(_utterances(["a", "b", "c", "d"])) == 2


def test_speaker_count_ignores_blank_and_missing_codes():
    turns = [{"speaker": "A", "text": "x", "start": 0, "end": 1},
             {"speaker": None, "text": "y", "start": 1, "end": 2},
             {"speaker": "  ", "text": "z", "start": 2, "end": 3}]
    assert qt.speaker_count(turns) == 1


def test_speaker_count_with_no_utterances_is_zero():
    assert qt.speaker_count([]) == 0
    assert qt.speaker_count(None) == 0
