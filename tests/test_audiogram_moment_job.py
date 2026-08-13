"""
Unit tests for the standalone longer-form audiogram job (KH-AUD-001, action="audiogram").

Covers the pure, network-free parts: action="audiogram" payload validation, the
detect.py duration-band widening (build_candidates/score_candidate/detect stay
byte-identical for every EXISTING caller; the override only applies when a caller
opts in), and audiogram_band_override(). No Modal, no Supabase, no ffmpeg, no LLM
call needed.

    python -m pytest tests/test_audiogram_moment_job.py
"""
import importlib.util
import os
import sys
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import audiogram, detect


# ----------------------------------------------------------------------
# detect.py: band_override is additive — every existing call site (no override)
# must be completely unaffected.
# ----------------------------------------------------------------------
def _words(n_sentences=30, words_per=14):
    """A synthetic guest-only transcript: n_sentences clean sentences back to
    back, long enough to build candidates well past the Shorts 35s ceiling."""
    words = []
    t = 0.0
    line = ("I decided to change everything after that day and it mattered "
            "a great deal to me").split()
    for _ in range(n_sentences):
        for i, w in enumerate(line[:words_per]):
            text = w + ("." if i == words_per - 1 else "")
            words.append({"text": text, "start": t, "end": t + 0.3, "speaker": 1})
            t += 0.35
    return words


def test_audiogram_band_override_known_presets():
    assert detect.audiogram_band_override(30) == ((24, 30), (18, 30))
    assert detect.audiogram_band_override(60) == ((48, 60), (35, 60))
    assert detect.audiogram_band_override(90) == ((72, 90), (55, 90))
    assert detect.audiogram_band_override(120) == ((95, 120), (75, 120))


def test_audiogram_band_override_unknown_preset_is_none():
    assert detect.audiogram_band_override(45) is None
    assert detect.audiogram_band_override(999) is None


def test_build_candidates_default_unchanged_by_new_kwargs():
    sentences = detect.build_sentences(_words())
    baseline = detect.build_candidates(sentences, guest_speaker=1)
    explicit_none = detect.build_candidates(sentences, guest_speaker=1,
                                            min_len_sec=None, max_len_sec=None)
    assert baseline == explicit_none
    assert max((c["length_sec"] for c in baseline), default=0) <= detect.MAX_LEN_SEC


def test_build_candidates_widened_band_reaches_past_shorts_ceiling():
    sentences = detect.build_sentences(_words())
    wide = detect.build_candidates(sentences, guest_speaker=1,
                                   min_len_sec=35, max_len_sec=90)
    assert max((c["length_sec"] for c in wide), default=0) > detect.MAX_LEN_SEC
    assert all(c["length_sec"] >= 35 for c in wide)


def test_score_candidate_without_override_matches_legacy_behaviour():
    sentences = detect.build_sentences(_words())
    candidates = detect.build_candidates(sentences, guest_speaker=1)
    for c in candidates[:5]:
        a = detect.score_candidate(dict(c), clip_type="best")
        b = detect.score_candidate(dict(c), clip_type="best", band_override=None)
        assert a == b


def test_score_candidate_band_override_replaces_typed_band_not_rejects():
    # story_teaser's own Shorts allowed_band tops out at 35s — a 60s candidate
    # would be rejected under it, but is scoreable under a widened override.
    sentences = detect.build_sentences(_words())
    wide = detect.build_candidates(sentences, guest_speaker=1,
                                   min_len_sec=35, max_len_sec=60)
    long_candidate = next((c for c in wide if c["length_sec"] > 35), None)
    assert long_candidate is not None
    unbounded = detect.score_candidate(dict(long_candidate), clip_type="story_teaser")
    assert unbounded is None            # rejected under story_teaser's own 16-35s band
    override = ((48, 60), (35, 60))
    widened = detect.score_candidate(dict(long_candidate), clip_type="story_teaser",
                                     band_override=override)
    assert widened is not None
    assert widened["fit_score"] > 0


def test_detect_signature_band_override_defaults_to_none():
    import inspect
    sig = inspect.signature(detect.detect)
    assert sig.parameters["band_override"].default is None


# ----------------------------------------------------------------------
# worker/app.py: validate_audiogram_payload (Modal/fastapi stubbed out, mirrors
# test_audiogram_landscape.py's _load_worker_app pattern).
# ----------------------------------------------------------------------
def _load_worker_app():
    for mod in ("modal", "fastapi"):
        sys.modules.setdefault(mod, MagicMock())
    spec = importlib.util.spec_from_file_location(
        "worker_app_under_test_audiogram", os.path.join(ROOT, "worker", "app.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(**over):
    p = {"action": "audiogram", "job_id": "3e1c2f9a-0000-4000-8000-000000000000",
         "url": "https://www.youtube.com/watch?v=iTX6b2Z01II",
         "series": "kintsugi-heroes", "clip_type": "hero_today", "duration_sec": 60,
         "transcript": {"title": "EP 14", "words": [{"text": "Hi", "start": 0, "end": 0.4}]}}
    p.update(over)
    return p


def test_valid_audiogram_payload_passes():
    app = _load_worker_app()
    assert app.validate_audiogram_payload(_payload()) is None


def test_missing_required_fields_are_rejected():
    app = _load_worker_app()
    for field in ("job_id", "url", "series"):
        p = _payload()
        del p[field]
        assert field in app.validate_audiogram_payload(p)
        p[field] = "   "
        assert field in app.validate_audiogram_payload(p)


def test_duration_sec_must_be_a_known_preset():
    app = _load_worker_app()
    v = app.validate_audiogram_payload
    assert v(_payload(duration_sec=45)) is not None
    assert v(_payload(duration_sec=True)) is not None      # bool is not a valid preset
    for d in (30, 60, 90, 120):
        assert v(_payload(duration_sec=d)) is None
    p = _payload()
    del p["duration_sec"]
    assert v(p) is None                                     # absent -> defaults to 60


def test_transcript_with_words_is_required():
    app = _load_worker_app()
    v = app.validate_audiogram_payload
    p = _payload()
    del p["transcript"]
    assert v(p) is not None
    assert v(_payload(transcript={})) is not None
    assert v(_payload(transcript={"words": []})) is not None
    assert v(_payload(transcript="nope")) is not None


def test_non_dict_payload_is_rejected():
    app = _load_worker_app()
    assert app.validate_audiogram_payload(None) is not None
    assert app.validate_audiogram_payload("nope") is not None


def test_audiogram_duration_presets_match_detect_module():
    app = _load_worker_app()
    from src import detect as detect_mod
    assert app.AUDIOGRAM_DURATION_SEC == set(detect_mod.AUDIOGRAM_DURATION_PRESETS.keys())


# ----------------------------------------------------------------------
# audiogram.render(): timed_captions is opt-in — default behaviour (every
# existing Shorts caller) must be untouched; render_landscape's own
# group_caption_lines timing is what timed_captions=True reuses.
# ----------------------------------------------------------------------
def _render_words():
    return [{"text": "Hi", "start": 0.0, "end": 0.3},
            {"text": "there,", "start": 0.3, "end": 0.6},
            {"text": "friend.", "start": 0.6, "end": 1.0}]


def test_render_default_passes_no_timed_lines():
    with patch.object(audiogram, "_render") as mock_render, \
         patch.object(audiogram, "_audio_pcm", return_value=None), \
         patch.object(audiogram, "_duration", return_value=5.0):
        audiogram.render("clip.mp4", _render_words(), "/tmp/out", series="kintsugi-heroes")
    assert mock_render.call_count == 2                     # square + vertical
    for call in mock_render.call_args_list:
        assert call.kwargs["timed_lines"] is None


def test_render_timed_captions_true_builds_timed_lines():
    with patch.object(audiogram, "_render") as mock_render, \
         patch.object(audiogram, "_audio_pcm", return_value=None), \
         patch.object(audiogram, "_duration", return_value=5.0):
        audiogram.render("clip.mp4", _render_words(), "/tmp/out", series="kintsugi-heroes",
                         timed_captions=True)
    assert mock_render.call_count == 2
    for call in mock_render.call_args_list:
        assert call.kwargs["timed_lines"] is not None


def test_render_timed_captions_true_with_no_words_stays_static():
    with patch.object(audiogram, "_render") as mock_render, \
         patch.object(audiogram, "_audio_pcm", return_value=None), \
         patch.object(audiogram, "_duration", return_value=5.0):
        audiogram.render("clip.mp4", [], "/tmp/out", series="kintsugi-heroes",
                         caption="static line", timed_captions=True)
    for call in mock_render.call_args_list:
        assert call.kwargs["timed_lines"] is None
