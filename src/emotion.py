"""
KH Clipper: audio-emotion signal for candidate scoring.

The detector (src/detect.py) is transcript-only, but the emotional peaks that
make a Kintsugi moment land are AUDIO phenomena: the voice shifting intensity,
a rise into a charged line, a held pause before the payoff. This module reads
those from the episode audio and turns them into a small, conservative BONUS
(0..8) that detect.py ADDS to a candidate's fit score.

Design rules (mirrors the guardrails two-layer model):
  - It is a bonus, never a gate. It can nudge an emotionally-delivered moment
    above a flat one; it can never rescue a candidate the safety gate, the
    specific-story gate or the off-theme penalty would reject.
  - Deterministic and dependency-light: numpy plus the same ffmpeg PCM
    extraction pattern as audiogram._audio_pcm (22050 Hz mono f32le).
  - Degrades to nothing: any failure (no ffmpeg, no numpy, unreadable file)
    returns None/0 and detect.py behaves exactly as it does today.

All features are normalised against the WHOLE-FILE median frame energy, so the
signal is about how this window moves relative to the episode's own baseline,
not about absolute recording level.
"""

import subprocess

SAMPLE_RATE = 22050        # matches audiogram._audio_pcm
FRAME_SEC = 0.5            # short-term energy frame length

# Held-pause band (seconds). Below 0.6s is ordinary speech rhythm; above 2.0s
# is technical dead air or an edit point, not a held emotional beat.
PAUSE_MIN_SEC = 0.6
PAUSE_MAX_SEC = 2.0

# ----------------------------------------------------------------------
# Bonus thresholds (documented, deterministic, conservative).
#
# Frame RMS is normalised by the whole-file median frame RMS, so a steady
# conversational read sits near 1.0 with variance well under 0.1.
#   - rms_var:  variance of the normalised frame series inside the window.
#               >= 0.15 means the energy genuinely moves (moderate);
#               >= 0.35 means a real intensity swing (strong).
#   - max_rise: largest jump between ADJACENT 0.5s frames, in units of the
#               file median energy. >= 0.5 (the voice steps up by half the
#               episode's typical energy in half a second) is moderate;
#               >= 1.0 is strong.
#   - pauses:   inter-word gaps in the 0.6..2.0s band are held beats. One or
#               more earns +2; a long hold (>= 1.0s) earns +1 more.
#
# A pause with a completely flat delivery is weak evidence on its own, so
# without any intensity signal the bonus is capped at PAUSE_ONLY_CAP.
# A flat reading (no variance, no rise, no held pause) yields exactly 0.
# ----------------------------------------------------------------------
VAR_MODERATE = 0.15
VAR_STRONG = 0.35
RISE_MODERATE = 0.5
RISE_STRONG = 1.0
INTENSITY_STRONG_PTS = 5
INTENSITY_MODERATE_PTS = 3
PAUSE_PTS = 2
LONG_PAUSE_SEC = 1.0
LONG_PAUSE_PTS = 1
PAUSE_ONLY_CAP = 2.0
BONUS_MAX = 8.0

_SILENT_MEDIAN = 1e-6      # below this the file is effectively silent


def load_pcm(wav_path, sr=SAMPLE_RATE):
    """Decode any audio file to mono f32 PCM at `sr` via ffmpeg (the same
    pattern as audiogram._audio_pcm). Returns a numpy float32 array, or None
    when decoding is not possible (missing ffmpeg, unreadable file)."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-v", "quiet", "-i", wav_path, "-f", "f32le", "-ac", "1",
             "-ar", str(sr), "-"], capture_output=True)
        import numpy as np
        pcm = np.frombuffer(r.stdout, dtype=np.float32)
        return pcm if pcm.size else None
    except Exception:
        return None


def _frame_rms(pcm, sr, frame_sec=FRAME_SEC):
    """Short-term RMS energy per non-overlapping frame. Returns a 1-D numpy
    array (possibly empty)."""
    import numpy as np
    n = int(sr * frame_sec)
    if n <= 0 or pcm is None or len(pcm) < n:
        return np.zeros(0, dtype=np.float64)
    usable = (len(pcm) // n) * n
    frames = pcm[:usable].astype(np.float64).reshape(-1, n)
    return np.sqrt(np.mean(frames * frames, axis=1))


def file_median_rms(pcm, sr=SAMPLE_RATE):
    """Whole-file median frame RMS, the normalisation baseline. Precompute this
    once per episode and pass it to window_features via file_median so a job
    with hundreds of candidate windows does not recompute it every time."""
    import numpy as np
    rms = _frame_rms(pcm, sr)
    return float(np.median(rms)) if rms.size else 0.0


def window_features(pcm, sr, start, end, words=None, file_median=None):
    """Audio features for one candidate window [start, end] (seconds into the
    episode audio, the same clock as the transcript word timings).

    Returns a dict:
      rms_var     variance of the window's 0.5s frame RMS, normalised by the
                  whole-file median frame RMS (a flat read is near 0)
      max_rise    max positive delta between adjacent normalised frames
      pause_count number of inter-word gaps in the 0.6..2.0s held-pause band,
                  from `words` (the candidate's word timings); 0 when absent
      pause_max   the longest such gap in seconds (0.0 when none)

    `file_median` is the precomputed file_median_rms; computed here when not
    supplied. A silent or unusable file yields all-zero features.
    """
    import numpy as np
    feats = {"rms_var": 0.0, "max_rise": 0.0, "pause_count": 0, "pause_max": 0.0}

    # Pause profile from word timings (pure, no audio needed).
    if words:
        prev_end = None
        for w in words:
            try:
                ws, we = float(w["start"]), float(w["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if we <= start or ws >= end:
                continue
            if prev_end is not None:
                gap = ws - prev_end
                if PAUSE_MIN_SEC <= gap <= PAUSE_MAX_SEC:
                    feats["pause_count"] += 1
                    feats["pause_max"] = max(feats["pause_max"], round(gap, 3))
            prev_end = we
    if pcm is None or len(pcm) == 0:
        return feats

    median = file_median if file_median is not None else file_median_rms(pcm, sr)
    if median <= _SILENT_MEDIAN:
        return feats

    lo = max(0, int(start * sr))
    hi = min(len(pcm), int(end * sr))
    if hi <= lo:
        return feats
    rms = _frame_rms(pcm[lo:hi], sr)
    if rms.size == 0:
        return feats
    norm = rms / median
    feats["rms_var"] = float(np.var(norm))
    if norm.size >= 2:
        deltas = np.diff(norm)
        feats["max_rise"] = float(max(0.0, np.max(deltas)))
    return feats


def emotion_bonus(features):
    """Map window features to a 0..8 additive score bonus. Deterministic and
    conservative: a flat reading yields 0, and a held pause without any
    intensity movement is capped at PAUSE_ONLY_CAP. Thresholds are documented
    at the top of this file. Pure dict maths, safe without numpy."""
    if not features:
        return 0.0
    var = float(features.get("rms_var", 0.0) or 0.0)
    rise = float(features.get("max_rise", 0.0) or 0.0)
    pauses = int(features.get("pause_count", 0) or 0)
    pause_max = float(features.get("pause_max", 0.0) or 0.0)

    intensity = 0.0
    if var >= VAR_STRONG or rise >= RISE_STRONG:
        intensity = INTENSITY_STRONG_PTS
    elif var >= VAR_MODERATE or rise >= RISE_MODERATE:
        intensity = INTENSITY_MODERATE_PTS

    pause = 0.0
    if pauses >= 1:
        pause = PAUSE_PTS
        if pause_max >= LONG_PAUSE_SEC:
            pause += LONG_PAUSE_PTS

    bonus = intensity + pause
    if intensity == 0.0:
        bonus = min(bonus, PAUSE_ONLY_CAP)
    return float(min(BONUS_MAX, max(0.0, bonus)))
