"""
Tests for exact-cut moments (Wave 1): the pure builder that turns board-approved
windows into detect-shaped clips, with the worker's own safety gate re-run on each
window and the cut snapped to word boundaries + clamped to MAX_CLIP_SEC.

    python -m pytest tests/test_moments.py
    python tests/test_moments.py        # also runs standalone (no pytest)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import moments


def _tdata(words_text, step=1.0, dur=0.9):
    """A fake transcript: each word at t=i*step for `dur` seconds."""
    return {
        "id": "vid123",
        "title": "Episode",
        "words": [
            {"text": w, "start": round(i * step, 2), "end": round(i * step + dur, 2), "speaker": 0}
            for i, w in enumerate(words_text.split())
        ],
    }


def test_validate_moments_rejects_bad_and_accepts_good():
    assert moments.validate_moments([]) is not None
    assert moments.validate_moments("nope") is not None
    assert moments.validate_moments([{"start": 5, "end": 3}]) is not None
    assert moments.validate_moments([{"start": "x", "end": 3}]) is not None
    assert moments.validate_moments([{"start": -1, "end": 3}]) is not None
    assert moments.validate_moments([{"start": 0, "end": 3}]) is None
    assert moments.validate_moments([{"start": 2.5, "end": 9.0, "hook_line": "hi"}]) is None
    # cap
    assert moments.validate_moments([{"start": i, "end": i + 1} for i in range(21)]) is not None


def test_builds_a_clip_snapped_to_word_boundaries():
    td = _tdata("So I walked out of the fire and never looked back today")
    clips = moments.build_moment_clips(td, [{"start": 2.0, "end": 6.0}])
    assert len(clips) == 1
    c = clips[0]
    assert c["clip_id"] == "vid123-01"
    assert c["source"] == "exact_cut"
    # Snapped to real word start/end, not the raw request.
    assert c["start"] == 2.0
    assert c["text"].startswith("walked")
    assert c["safety"] == "ok"


def test_clamps_to_max_clip_sec_trimming_the_end():
    # 60 words at 1s each -> a 60s span; the ceiling must trim the END.
    td = _tdata(" ".join(f"word{i}" for i in range(60)))
    clips = moments.build_moment_clips(td, [{"start": 0.0, "end": 59.0}], max_sec=35.0)
    c = clips[0]
    assert c["length_sec"] <= 35.0
    assert c["start"] == 0.0


def test_reruns_safety_gate_on_the_window():
    # A window whose transcript names a consent-trigger must come back 'review',
    # regardless of what any caller might claim.
    td = _tdata("everything was fine until the overdose changed how I saw my whole life")
    clips = moments.build_moment_clips(td, [{"start": 0.0, "end": 12.0}])
    c = clips[0]
    assert c["safety"] == "review"
    assert c["consent_required"] is True
    assert c["safety_note"]


def test_skips_a_window_with_no_words_never_invents_content():
    td = _tdata("one two three four five")  # ends ~5s
    clips = moments.build_moment_clips(td, [{"start": 500.0, "end": 520.0}])
    assert clips == []


def test_stamps_source_video_id_so_cut_can_download():
    # The bulk-drop bug: without source_video_id on each clip, cut.run has no
    # source to rebuild the download URL from and every exact-cut render dies
    # at the cut stage. The transcript id IS the video id on the YouTube path.
    td = _tdata("a story about rebuilding after everything fell apart at once")
    clips = moments.build_moment_clips(td, [{"start": 0.0, "end": 8.0}])
    assert clips[0]["source_video_id"] == "vid123"


def test_uses_supplied_hook_line_when_given():
    td = _tdata("the day I decided to start again from nothing at all")
    clips = moments.build_moment_clips(td, [{"start": 0.0, "end": 8.0, "hook_line": "Starting again"}])
    assert clips[0]["hook_line"] == "Starting again"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
