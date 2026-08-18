"""
KH Clipper: silence tightening with emotional-pause preservation.

Mid-clip technical dead air (a cough edit, a long think, a producer prompt cut
out upstream) plays at full length in today's clips and costs retention. This
module plans and applies a gentle compression of those gaps WITHOUT flattening
the emotional beats a Kintsugi story runs on.

Hard rules, all enforced in plan_tighten:
  - CALM clips are untouched: safety != "ok" plans nothing at all.
  - A pause is never removed, only compressed: every planned gap keeps
    KEEP_SEC of air (0.8s), so the rhythm of the delivery survives.
  - The gap directly before the clip's FINAL sentence is never touched. A
    held pause before the payoff line is a retention device, not dead air.
  - The first HOOK_ZONE_SEC (4s) of the clip are never touched. The hook zone
    is the engine of the whole clip; it ships exactly as detected.
  - Total removed time is capped at MAX_REMOVED_FRACTION (15%) of the clip.
    When gaps compete, the longest gaps (the most dead air per cut) win.

Time base: the PLAN is in CLIP-RELATIVE seconds (t=0 is the first frame of the
cut clip file), because apply_tighten operates on the cut file and remap feeds
caption/banner timings which are already clip-relative (caption.clip_words).
plan_tighten accepts transcript words in EPISODE time together with the clip's
[clip_start, clip_end] window and rebases internally; passing clip-relative
words with clip_start=0 works identically.

Honest limitations of apply_tighten: ffmpeg trim points snap to the decoded
frame grid (about 1/25 to 1/30 s), so a joint can land up to one frame off the
planned time. At sentence scale, against 0.4s+ of removed air per gap, that is
inaudible and invisible, but this is NOT a sample-accurate audio editor. The
output is re-encoded (libx264 crf 18), one extra generation loss before the
caption stage's own encode.
"""
from __future__ import annotations

import re
import shutil
import subprocess

MIN_GAP_SEC = 1.2           # only gaps longer than this are candidates
KEEP_SEC = 0.8              # every tightened gap keeps this much air
HOOK_ZONE_SEC = 4.0         # never plan a cut in the first 4 seconds
MAX_REMOVED_FRACTION = 0.15  # cap total removed time at 15% of clip duration

_SENTENCE_END = re.compile(r"[.!?][\"')\]]*$")


def _window_words(words, clip_start, clip_end):
    """Words inside the clip window, rebased to clip-relative time, in order."""
    out = []
    for w in words or []:
        try:
            ws, we = float(w["start"]), float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if we <= clip_start or ws >= clip_end:
            continue
        out.append({
            "text": str(w.get("text", "")),
            "start": max(0.0, ws - clip_start),
            "end": max(0.0, min(we, clip_end) - clip_start),
        })
    return out


def _final_sentence_first_index(words):
    """Index of the first word of the clip's LAST sentence, found via terminal
    punctuation. The last sentence starts right after the last word (excluding
    the final word itself) that ends with . ! or ?. When the clip is a single
    unpunctuated sentence, that is index 0 (and no gap can precede it)."""
    if not words:
        return 0
    last_terminal = -1
    for i, w in enumerate(words[:-1]):
        if _SENTENCE_END.search(w["text"].strip()):
            last_terminal = i
    return last_terminal + 1


def plan_tighten(words, clip_start, clip_end, safety):
    """Plan gap compressions for one clip. Returns a list of
    (gap_start, gap_end, keep_sec) tuples in CLIP-RELATIVE seconds, sorted by
    gap_start. Empty list = nothing to do (also the answer for any clip whose
    safety is not "ok": CALM clips ship untouched)."""
    if safety != "ok":
        return []
    clip_dur = float(clip_end) - float(clip_start)
    if clip_dur <= 0:
        return []
    win = _window_words(words, float(clip_start), float(clip_end))
    if len(win) < 2:
        return []
    payoff_idx = _final_sentence_first_index(win)

    candidates = []
    for i in range(1, len(win)):
        gap_start, gap_end = win[i - 1]["end"], win[i]["start"]
        gap = gap_end - gap_start
        if gap <= MIN_GAP_SEC:
            continue
        if gap_start < HOOK_ZONE_SEC:
            continue                     # the hook zone ships exactly as detected
        if i == payoff_idx:
            continue                     # held pause before the payoff: keep it
        candidates.append((gap_start, gap_end, KEEP_SEC))

    # 15% budget: longest gaps first (most dead air removed per cut), drop the
    # rest once the budget is spent.
    budget = MAX_REMOVED_FRACTION * clip_dur
    removed = 0.0
    kept = []
    for cand in sorted(candidates, key=lambda c: c[1] - c[0], reverse=True):
        cut = (cand[1] - cand[0]) - cand[2]
        if removed + cut > budget:
            continue
        removed += cut
        kept.append(cand)
    return sorted(kept, key=lambda c: c[0])


def remap(t, plan):
    """Map a CLIP-RELATIVE original time to its time in the tightened clip.
    Times inside a removed span collapse onto the end of the kept air, so an
    event timed mid-gap never lands past the material around it."""
    t = float(t)
    shift = 0.0
    for gap_start, gap_end, keep_sec in plan:
        cut_from = gap_start + keep_sec
        if t <= cut_from:
            break                         # plan is sorted; nothing later applies
        shift += min(t, gap_end) - cut_from
    return round(t - shift, 3)


def remap_words(words, plan):
    """Word list with start/end retimed into the tightened clip's clock, for
    the caption/banner events downstream. Input words are clip-relative (the
    caption.clip_words shape); order and text are preserved."""
    if not plan:
        return list(words or [])
    out = []
    for w in words or []:
        nw = dict(w)
        nw["start"] = remap(w["start"], plan)
        nw["end"] = remap(w["end"], plan)
        out.append(nw)
    return out


def apply_tighten(src_mp4, plan, out_mp4):
    """Render the tightened clip: keep-segments trimmed and concatenated with
    one ffmpeg filter graph, re-encoded (libx264 -preset medium -crf 18,
    yuv420p, aac audio). An empty plan copies the file through unchanged, so
    callers can pipe every clip here without branching. Frame-accurate to the
    decoded frame grid (see the module docstring), which is plenty at sentence
    scale. Returns out_mp4."""
    if not plan:
        shutil.copyfile(src_mp4, out_mp4)
        return out_mp4

    # Keep-segments between the removed spans. The removed span of each gap is
    # [gap_start + keep_sec, gap_end]; the final segment runs to end of file.
    segments = []
    pos = 0.0
    for gap_start, gap_end, keep_sec in plan:
        segments.append((pos, gap_start + keep_sec))
        pos = gap_end
    segments.append((pos, None))
    segments = [(a, b) for a, b in segments if b is None or b - a > 0.01]

    parts, maps = [], []
    for i, (a, b) in enumerate(segments):
        span = f"start={a:.3f}" + ("" if b is None else f":end={b:.3f}")
        parts.append(f"[0:v]trim={span},setpts=PTS-STARTPTS[v{i}]")
        parts.append(f"[0:a]atrim={span},asetpts=PTS-STARTPTS[a{i}]")
        maps.append(f"[v{i}][a{i}]")
    graph = (";".join(parts)
             + f";{''.join(maps)}concat=n={len(segments)}:v=1:a=1[vo][ao]")
    cmd = [
        "ffmpeg", "-y", "-i", src_mp4, "-filter_complex", graph,
        "-map", "[vo]", "-map", "[ao]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart",
        out_mp4,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-1200:])
    return out_mp4


def total_removed(plan):
    """Seconds of dead air the plan removes. Handy for logs and REVIEW.md."""
    return round(sum((ge - gs) - keep for gs, ge, keep in plan), 3)
