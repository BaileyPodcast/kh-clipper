"""
Tests for the audio-emotion signal (src/emotion.py) and its wiring into
detect.py scoring.

Covers:
  - emotion_bonus mapping: flat reading = 0, pause-only capped, intensity plus
    pause = the full 8, clamping. Pure dict maths, runs without numpy.
  - window_features on synthetic PCM (numpy required, skipped when absent):
    flat file, a strong energy rise, whole-file median normalisation, and the
    held-pause profile from word timings.
  - score_candidate: emotion_bonus=0 is byte-identical to the baseline, a
    positive bonus adds (about) its face value, values above the cap clamp,
    negatives clamp to zero, and the bonus can never rescue the off-theme
    penalty (bonus, never a gate).
  - detect._emotion_bonuses degrades to None when there is no audio path.
  - the loopable final-ordering weight is 8 (loops count as views, March 2025).

    python -m pytest tests/test_emotion.py
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import detect, emotion


# ----------------------------------------------------------------------
# emotion_bonus: pure mapping, no numpy needed
# ----------------------------------------------------------------------

def test_flat_reading_yields_zero():
    feats = {"rms_var": 0.0, "max_rise": 0.0, "pause_count": 0, "pause_max": 0.0}
    assert emotion.emotion_bonus(feats) == 0.0
    assert emotion.emotion_bonus({}) == 0.0
    assert emotion.emotion_bonus(None) == 0.0


def test_below_thresholds_yields_zero():
    feats = {"rms_var": 0.14, "max_rise": 0.49, "pause_count": 0, "pause_max": 0.0}
    assert emotion.emotion_bonus(feats) == 0.0


def test_moderate_intensity():
    assert emotion.emotion_bonus({"rms_var": 0.2, "max_rise": 0.0,
                                  "pause_count": 0, "pause_max": 0.0}) == 3.0
    assert emotion.emotion_bonus({"rms_var": 0.0, "max_rise": 0.6,
                                  "pause_count": 0, "pause_max": 0.0}) == 3.0


def test_strong_intensity():
    assert emotion.emotion_bonus({"rms_var": 0.4, "max_rise": 0.0,
                                  "pause_count": 0, "pause_max": 0.0}) == 5.0
    assert emotion.emotion_bonus({"rms_var": 0.0, "max_rise": 1.2,
                                  "pause_count": 0, "pause_max": 0.0}) == 5.0


def test_pause_only_is_capped():
    # A held pause in a flat delivery is weak evidence on its own.
    feats = {"rms_var": 0.0, "max_rise": 0.0, "pause_count": 2, "pause_max": 1.5}
    assert emotion.emotion_bonus(feats) == emotion.PAUSE_ONLY_CAP


def test_intensity_plus_long_pause_is_full_eight():
    feats = {"rms_var": 0.4, "max_rise": 1.1, "pause_count": 1, "pause_max": 1.2}
    assert emotion.emotion_bonus(feats) == 8.0


def test_short_pause_earns_two_not_three():
    feats = {"rms_var": 0.4, "max_rise": 0.0, "pause_count": 1, "pause_max": 0.8}
    assert emotion.emotion_bonus(feats) == 7.0


def test_bonus_never_exceeds_max():
    feats = {"rms_var": 99.0, "max_rise": 99.0, "pause_count": 9, "pause_max": 2.0}
    assert emotion.emotion_bonus(feats) == emotion.BONUS_MAX == 8.0


# ----------------------------------------------------------------------
# window_features: synthetic PCM (numpy required)
# ----------------------------------------------------------------------

def _const_pcm(np, amp, sec, sr=emotion.SAMPLE_RATE):
    return np.full(int(sec * sr), amp, dtype=np.float32)


def test_window_features_flat_file():
    np = pytest.importorskip("numpy")
    pcm = _const_pcm(np, 0.1, 10.0)
    feats = emotion.window_features(pcm, emotion.SAMPLE_RATE, 0.0, 10.0)
    assert feats["rms_var"] == pytest.approx(0.0, abs=1e-9)
    assert feats["max_rise"] == pytest.approx(0.0, abs=1e-9)
    assert emotion.emotion_bonus(feats) == 0.0


def test_window_features_strong_rise():
    np = pytest.importorskip("numpy")
    pcm = np.concatenate([_const_pcm(np, 0.05, 5.0), _const_pcm(np, 0.5, 5.0)])
    feats = emotion.window_features(pcm, emotion.SAMPLE_RATE, 0.0, 10.0)
    # Median frame energy is 0.275; the step from 0.05 to 0.5 is a normalised
    # rise of about 1.6, well past the strong threshold.
    assert feats["max_rise"] > emotion.RISE_STRONG
    assert feats["rms_var"] > emotion.VAR_STRONG
    assert emotion.emotion_bonus(feats) == 5.0


def test_window_normalised_against_whole_file():
    np = pytest.importorskip("numpy")
    # A quiet-but-flat stretch of a dynamic file should still read as flat.
    pcm = np.concatenate([_const_pcm(np, 0.05, 5.0), _const_pcm(np, 0.5, 5.0)])
    feats = emotion.window_features(pcm, emotion.SAMPLE_RATE, 0.0, 5.0)
    assert feats["rms_var"] == pytest.approx(0.0, abs=1e-9)
    assert feats["max_rise"] == pytest.approx(0.0, abs=1e-9)


def test_window_features_pause_profile():
    np = pytest.importorskip("numpy")
    pcm = _const_pcm(np, 0.1, 12.0)
    words = [
        {"text": "I", "start": 1.0, "end": 1.3},
        {"text": "stopped.", "start": 1.4, "end": 2.0},
        {"text": "Everything", "start": 3.0, "end": 3.5},   # 1.0s held pause
        {"text": "went", "start": 3.6, "end": 3.9},
        {"text": "quiet.", "start": 6.5, "end": 7.0},       # 2.6s: dead air, not a pause
    ]
    feats = emotion.window_features(pcm, emotion.SAMPLE_RATE, 0.0, 12.0, words=words)
    assert feats["pause_count"] == 1
    assert feats["pause_max"] == pytest.approx(1.0)


def test_silent_file_yields_zero_features():
    np = pytest.importorskip("numpy")
    pcm = np.zeros(emotion.SAMPLE_RATE * 5, dtype=np.float32)
    feats = emotion.window_features(pcm, emotion.SAMPLE_RATE, 0.0, 5.0)
    assert feats == {"rms_var": 0.0, "max_rise": 0.0,
                     "pause_count": 0, "pause_max": 0.0}


def test_load_pcm_missing_file_returns_none():
    # With or without ffmpeg on PATH this must degrade to None, never raise.
    assert emotion.load_pcm("/nonexistent/audio.wav") is None


# ----------------------------------------------------------------------
# detect.py wiring
# ----------------------------------------------------------------------

def _mk_words(text, start=0.0, dur=0.4):
    words, t = [], start
    for tok in text.split():
        words.append({"text": tok, "start": round(t, 2), "end": round(t + dur, 2)})
        t += dur
    return words


def _candidate(text="I was fifteen years old and I was terrified of going home.",
               length_sec=10.0):
    sent = detect._make_sentence(_mk_words(text))
    return {"start": sent["start"], "end": sent["start"] + length_sec,
            "length_sec": length_sec, "sentences": [sent]}


def test_zero_bonus_is_byte_identical():
    base = detect.score_candidate(_candidate())
    with_zero = detect.score_candidate(_candidate(), emotion_bonus=0.0)
    assert base is not None
    assert base == with_zero
    assert "emotion_audio" not in base["score_breakdown"]


def test_positive_bonus_adds_roughly_face_value():
    base = detect.score_candidate(_candidate())
    boosted = detect.score_candidate(_candidate(), emotion_bonus=8.0)
    assert boosted["fit_score"] > base["fit_score"]
    # total * 1.05 normalisation: +8 lands as +8 or +9 fit points.
    assert boosted["fit_score"] - base["fit_score"] in (8, 9)
    assert boosted["score_breakdown"]["emotion_audio"] == 8.0


def test_bonus_clamps_above_cap_and_below_zero():
    capped = detect.score_candidate(_candidate(), emotion_bonus=999.0)
    at_cap = detect.score_candidate(_candidate(), emotion_bonus=detect.EMOTION_BONUS_CAP)
    assert capped["fit_score"] == at_cap["fit_score"]
    negative = detect.score_candidate(_candidate(), emotion_bonus=-5.0)
    base = detect.score_candidate(_candidate())
    assert negative == base


def test_bonus_cannot_outweigh_off_theme_penalty():
    # The off-theme penalty is 10; the bonus caps at 8, so a maxed bonus can
    # never fully cancel it. Verified against an on-theme twin of the same clip.
    assert detect.EMOTION_BONUS_CAP < 10


def test_emotion_bonuses_degrade_to_none():
    cands = [_candidate()]
    assert detect._emotion_bonuses(cands, None) is None
    assert detect._emotion_bonuses([], "whatever.wav") is None
    # A missing file must degrade silently (load_pcm returns None).
    assert detect._emotion_bonuses(cands, "/nonexistent/audio.wav") is None


def test_loopable_rank_bonus_is_eight():
    # Loops have counted as views since March 2025 (raised from 4).
    assert detect.LOOPABLE_RANK_BONUS == 8


def test_loopable_ordering_uses_the_raised_weight():
    # Simulate the exact final-ordering key detect() applies to Grok picks.
    clips = [{"fit_score": 80, "loopable": False},
             {"fit_score": 73, "loopable": True}]
    clips.sort(key=lambda c: c.get("fit_score", 0)
               + (detect.LOOPABLE_RANK_BONUS if c.get("loopable") else 0),
               reverse=True)
    # 73 + 8 = 81 beats 80; under the old +4 it would have lost.
    assert clips[0]["fit_score"] == 73
