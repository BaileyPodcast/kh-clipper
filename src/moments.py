"""
Exact-cut moments (Wave 1): render a board-approved window instead of the
worker's own auto-selected moment.

The Shorts Engine's auto-select (detect + rerank) stays the default. When the
caller passes an explicit list of moments ({start, end, hook_line?}), this module
turns each into a clip in the SAME shape detect.detect() produces, so the rest of
the pipeline (metadata, cut, reframe, caption, brand) runs unchanged and the clip
comes out packaged exactly like an auto-selected one.

Two guarantees the exact-cut route must keep (KH-TIC-001 + cut quality):
  1. The worker re-runs its OWN safety gate on the chosen window
     (detect.assess_safety), never trusting a caller-supplied flag. The exact-cut
     route can't bypass the trauma-informed gate.
  2. The window is snapped to real word boundaries and clamped to the hard
     MAX_CLIP_SEC ceiling (trim the end, keep the hook), so a specified window
     cuts as cleanly as an auto-selected one.

Pure + dependency-free (no Modal, no ffmpeg), so it is unit-testable directly.
"""

try:
    from . import detect
except ImportError:  # run as a script
    import detect

try:
    from . import cut as _cut
    MAX_CLIP_SEC = _cut.MAX_CLIP_SEC
except Exception:  # cut imports heavy deps in some contexts; fall back to the constant
    MAX_CLIP_SEC = 35.0


def validate_moments(moments):
    """Return None when every moment is well formed, else a short reason string.
    Pure, so the endpoint and the tests share one gate. A moment needs numeric
    start/end with start < end; hook_line is optional."""
    if not isinstance(moments, list) or not moments:
        return "moments must be a non-empty list"
    if len(moments) > 20:
        return "too many moments (max 20)"
    for i, m in enumerate(moments):
        if not isinstance(m, dict):
            return f"moment {i} is not an object"
        try:
            start = float(m.get("start"))
            end = float(m.get("end"))
        except (TypeError, ValueError):
            return f"moment {i} needs numeric start and end (seconds)"
        if start < 0 or end <= start:
            return f"moment {i} needs 0 <= start < end"
    return None


def _window_words(words, start, end):
    """The word-objects whose midpoint falls within [start, end]."""
    out = []
    for w in words:
        try:
            ws, we = float(w["start"]), float(w["end"])
        except (TypeError, KeyError, ValueError):
            continue
        mid = (ws + we) / 2.0
        if start <= mid <= end:
            out.append(w)
    return out


def _snap_and_clamp(words, req_start, req_end, max_sec):
    """Snap the requested window to the covered word boundaries and clamp to the
    hard ceiling (trim the END, keep the hook). Returns (start, end, window_words)
    or (None, None, []) when no words fall inside."""
    win = _window_words(words, req_start, req_end)
    if not win:
        return None, None, []
    start = float(win[0]["start"])
    end = float(win[-1]["end"])
    if end - start > max_sec:
        end = start + max_sec
        # Re-trim the word list to the clamped end so the text matches the cut.
        win = [w for w in win if float(w["end"]) <= end + 0.01] or win[:1]
        end = float(win[-1]["end"])
    return round(start, 2), round(end, 2), win


def _archetype_for(length_sec):
    for name, lo, hi in detect.ARCHETYPES:
        if lo <= length_sec <= hi:
            return name
    return "Story Teaser"


def _hook_from(text, supplied):
    supplied = (supplied or "").strip()
    if supplied:
        return supplied
    # First sentence, or the first ~10 words, as a sensible fallback hook line.
    for stop in (". ", "! ", "? "):
        if stop in text:
            return text.split(stop)[0].strip()
    return " ".join(text.split()[:10]).strip()


def build_moment_clips(tdata, moments, max_sec=MAX_CLIP_SEC, episode_id=None):
    """Turn caller-specified moments into detect-shaped clips. `tdata` is the
    loaded transcript (needs `words` with text/start/end). Skips any moment whose
    window resolves to no words (a bad timestamp), never inventing content."""
    words = tdata.get("words") or []
    vid = episode_id or tdata.get("id") or "episode"
    clips = []
    for i, m in enumerate(moments):
        start, end, win = _snap_and_clamp(words, float(m["start"]), float(m["end"]), max_sec)
        if start is None:
            continue
        text = " ".join(w["text"] for w in win).strip()
        length = round(end - start, 2)
        # KH-TIC-001: the worker's OWN safety read on the chosen window, never a
        # caller-supplied flag.
        safety, safety_note = detect.assess_safety(text)
        clips.append({
            "clip_id": f"{vid}-{i + 1:02d}",
            "start": start,
            "end": end,
            "length_sec": length,
            "archetype": _archetype_for(length),
            "hook_line": _hook_from(text, m.get("hook_line")),
            "hook_formula": "none",
            "loopable": False,
            "safety": safety,
            "safety_note": safety_note,
            "consent_required": safety != "ok",
            "consent_ok": False,
            "text": text,
            "source": "exact_cut",
        })
    return clips
