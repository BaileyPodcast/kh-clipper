"""
KH episode QC: the transcript-based checks (KH-QC-001).

Everything here is PURE. No ffmpeg, no network, no file IO: a transcript goes
in, findings come out. That is deliberate, because these are the checks that
carry real consequences (a no-go topic left in the final cut is a consent
breach, not a quality note) and they have to be testable to the letter.

Input shapes, reused verbatim from what kh-studio already sends the Shorts
worker rather than invented here:
  words       - the ClipperTranscript word list (lib/studio/shorts-transcript.ts):
                {"text": "Before", "start": 1.074, "end": 1.395, "speaker": 0}.
                The key is `text`, not `word`.
  utterances  - the diarised turns from studio_episodes.transcript_utterances:
                {"speaker": "A", "text": "...", "start": 1.07, "end": 10.6}.
                Speaker is AssemblyAI's bare letter code, so the speaker count
                and the episode shape check read THIS, never words[].speaker.
"""
from __future__ import annotations

import re

# Cut tolerance: a cut landing within this many seconds of a word edge is a
# clean edit. Word alignment is fuzzy at boundaries, which is exactly why the
# mid_word_cut check carries the lowest confidence in the contract table.
CUT_TOLERANCE_SEC = 0.12

# Duplicate detection. Six words is long enough that an exact repeat is worth a
# look; anything shorter is ordinary speech ("and I said to him").
DUPLICATE_WINDOW_SEC = 90
DUPLICATE_MIN_PHRASE_WORDS = 6

# The transcript/media fit tolerance, taken verbatim from worker/app.py's
# _prepare_supplied_transcript so QC and the Shorts path never disagree about
# whether a stored transcript belongs to a given file.
FIT_TRANSCRIPT_SLACK = 1.05
FIT_MEDIA_MAX_RATIO = 2.0


def _text(item):
    """The spoken text of a word or an utterance. Both shapes use `text`."""
    value = (item or {}).get("text")
    return value if isinstance(value, str) else ""


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def last_word_end(words):
    """The end time of the last timed word, or 0.0. Pure."""
    ends = [_num(w.get("end")) for w in (words or []) if isinstance(w, dict)]
    return max([e for e in ends if e is not None], default=0.0)


# ----------------------------------------------------------------------
# no-go topics (KH-TIC-001 3.3). The one check that blocks a publish.
# ----------------------------------------------------------------------
def _escape(term):
    return re.escape(term)


def no_go_hits(words, terms):
    """Every place a consent no-go term is actually SPOKEN in the final cut,
    with a real timestamp so a producer can go straight to it.

    Matching is WHOLE WORD or whole phrase, never substring, ported from
    textLeaksName in kh-studio's lib/studio/publish-gate.ts: a regex with \\b
    boundaries, case-insensitive, with regex metacharacters escaped. Substring
    matching is not a safer default here, it is a wrong one. A short term like
    "Pip" sits inside "epiphanies" and "pipeline", and exactly that false
    positive blocked a real publish on a hero's own verbatim quote (2026-08-13).
    On a 6000 word transcript it would fire constantly and train people to
    ignore the one check that must never be ignored.

    Multi-word terms are matched across CONSECUTIVE words so each hit still
    carries a genuine (start, end) span rather than a whole-transcript flag.

    Returns [{term, text, start, end}] ordered by start. Pure."""
    words = [w for w in (words or []) if isinstance(w, dict) and _text(w)]
    clean_terms = []
    for term in (terms or []):
        if not isinstance(term, str):
            continue
        t = term.strip()
        if t:
            clean_terms.append(t)
    if not words or not clean_terms:
        return []

    hits, seen = [], set()
    for term in clean_terms:
        tokens = term.split()
        span = max(len(tokens), 1)
        pattern = re.compile(rf"\b{_escape(term)}\b", re.IGNORECASE)
        for i in range(len(words) - span + 1):
            window = words[i:i + span]
            # Joined with single spaces so a phrase spanning words still gets
            # boundary anchoring at both ends of the match.
            phrase = " ".join(_text(w) for w in window)
            if not pattern.search(phrase):
                continue
            start = _num(window[0].get("start"))
            end = _num(window[-1].get("end"))
            key = (term.lower(), start)
            if key in seen:
                continue
            seen.add(key)
            hits.append({"term": term, "text": phrase,
                         "start": start if start is not None else 0.0,
                         "end": end if end is not None else start or 0.0})
    hits.sort(key=lambda h: h["start"])
    return hits


# ----------------------------------------------------------------------
# Cuts landing inside a word rather than in the gap between two.
# ----------------------------------------------------------------------
def mid_word_cuts(words, cut_points, tolerance=CUT_TOLERANCE_SEC):
    """Cut points that land INSIDE a spoken word instead of in the gap between
    words, which is what a clipped syllable at a splice sounds like. A cut
    within `tolerance` of either edge counts as clean, because word alignment
    is only accurate to about that much. Returns [{time, word, start, end}]. Pure."""
    words = [w for w in (words or []) if isinstance(w, dict)]
    out = []
    for point in (cut_points or []):
        t = _num(point)
        if t is None:
            continue
        for w in words:
            start, end = _num(w.get("start")), _num(w.get("end"))
            if start is None or end is None or end <= start:
                continue
            if start + tolerance <= t <= end - tolerance:
                out.append({"time": t, "word": _text(w), "start": start, "end": end})
                break
    return out


# ----------------------------------------------------------------------
# The same take left in twice.
# ----------------------------------------------------------------------
def _normalise(word):
    """Lowercase, punctuation stripped, so "said," and "Said" are one token."""
    return re.sub(r"[^a-z0-9']", "", _text(word).lower())


def duplicate_segments(words, window_sec=DUPLICATE_WINDOW_SEC,
                       min_phrase_words=DUPLICATE_MIN_PHRASE_WORDS):
    """The same run of `min_phrase_words` words appearing twice within
    `window_sec` of each other, which is usually a duplicate take left in the
    edit. Confidence is deliberately low in the contract table (0.7) because
    people do repeat themselves, so this is a look-here flag, not a verdict.

    Only the FIRST repeat of a given phrase is reported, and indices covered by
    a reported repeat are skipped, so one duplicated paragraph produces one
    finding instead of a wall of overlapping ones. Returns
    [{phrase, first_start, first_end, second_start, second_end}]. Pure."""
    words = [w for w in (words or []) if isinstance(w, dict) and _normalise(w)]
    if len(words) < min_phrase_words * 2 or min_phrase_words < 1:
        return []

    first_seen, out, skip_until = {}, [], -1
    for i in range(len(words) - min_phrase_words + 1):
        phrase_words = words[i:i + min_phrase_words]
        key = " ".join(_normalise(w) for w in phrase_words)
        prior = first_seen.get(key)
        if prior is None:
            first_seen[key] = i
            continue
        if i < skip_until or i < prior + min_phrase_words:
            continue                       # self-overlap, or inside a reported run
        first_start = _num(words[prior].get("start")) or 0.0
        second_start = _num(phrase_words[0].get("start")) or 0.0
        if second_start - first_start > window_sec:
            continue                       # too far apart to be one duplicated take
        out.append({
            "phrase": " ".join(_text(w) for w in phrase_words),
            "first_start": first_start,
            "first_end": _num(words[prior + min_phrase_words - 1].get("end")) or first_start,
            "second_start": second_start,
            "second_end": _num(phrase_words[-1].get("end")) or second_start,
        })
        skip_until = i + min_phrase_words
    return out


# ----------------------------------------------------------------------
# The KH episode shape. A heuristic, and it says so.
# ----------------------------------------------------------------------
SEGMENT_ORDER_CONFIDENCE = 0.5

EPISODE_SHAPE = ["hook", "branded_intro", "content_advisory",
                 "main_conversation", "outro"]

# Keyword sets, matched case-insensitively against each turn. These are what a
# KH episode actually says at each stage, but they are still just keywords: a
# host who words the advisory differently reads as missing. That is why this
# check sits at 0.5 and reports what it looked for.
SHAPE_KEYWORDS = {
    "branded_intro": ["kintsugi heroes", "welcome to kintsugi", "kintsugi podcast"],
    "content_advisory": ["content warning", "content advisory", "this episode discusses",
                         "this conversation discusses", "support is available",
                         "lifeline", "1800respect", "13 11 14", "if this raises"],
    "outro": ["thanks for listening", "thank you for listening", "thanks for joining",
              "until next time", "see you next time", "next episode", "subscribe"],
}

# A real conversation, not two turns of housekeeping between the intro and outro.
MAIN_CONVERSATION_MIN_TURNS = 10


def segment_order(utterances):
    """Check the diarised turns against the KH episode shape: hook, branded
    intro, content advisory, main conversation, outro.

    This is a KEYWORD HEURISTIC, not a verdict. It reports what it found, what
    it did not find and what looked out of order, and carries confidence 0.5 so
    the UI can say so. A missing stage here means "the words we look for were
    not said", never "the stage is missing".

    Returns {confidence, heuristic, found, missing, out_of_order, turns}. Pure."""
    turns = [u for u in (utterances or []) if isinstance(u, dict)]
    result = {"confidence": SEGMENT_ORDER_CONFIDENCE, "heuristic": True,
              "found": {}, "missing": [], "out_of_order": [], "turns": len(turns)}
    if not turns:
        result["missing"] = list(EPISODE_SHAPE)
        return result

    lowered = [_text(u).lower() for u in turns]
    found = {stage: None for stage in EPISODE_SHAPE}
    for stage, keywords in SHAPE_KEYWORDS.items():
        for i, text in enumerate(lowered):
            if any(k in text for k in keywords):
                found[stage] = i
                break

    # The hook is whatever is said BEFORE the branded intro, so it is positional
    # rather than keyed: no keyword can identify a good cold open.
    intro_at = found["branded_intro"]
    if intro_at is not None and intro_at > 0:
        found["hook"] = 0
    elif intro_at is None and lowered:
        found["hook"] = 0

    # The main conversation is the body between the last piece of top matter and
    # the outro: enough turns to be an actual conversation.
    body_start = max([v for v in (found["branded_intro"], found["content_advisory"])
                      if v is not None], default=-1) + 1
    body_end = found["outro"] if found["outro"] is not None else len(turns)
    if body_end - body_start >= MAIN_CONVERSATION_MIN_TURNS:
        found["main_conversation"] = body_start

    result["found"] = found
    result["missing"] = [s for s in EPISODE_SHAPE if found.get(s) is None]

    ordered = [(s, found[s]) for s in EPISODE_SHAPE if found.get(s) is not None]
    for (a_stage, a_at), (b_stage, b_at) in zip(ordered, ordered[1:]):
        if b_at < a_at:
            result["out_of_order"].append(f"{b_stage} appears before {a_stage}")
    return result


# ----------------------------------------------------------------------
# Does the stored transcript belong to this file at all.
# ----------------------------------------------------------------------
def transcript_fits_media(words, media_duration):
    """True when the stored transcript's timeline fits the media in hand.

    The tolerance is taken verbatim from worker/app.py's
    _prepare_supplied_transcript, so QC and the Shorts path can never disagree:
    the transcript must end inside the media (5% slack) and the media must not
    be a wildly different length. An unknown media duration returns True,
    matching that helper's `if dur and not (...)` behaviour, which trusts
    provenance when there is nothing to contradict it. The worker reports the
    unknown duration as its own info finding rather than hiding it in here. Pure."""
    last_end = last_word_end(words)
    if last_end <= 0:
        return False
    dur = _num(media_duration)
    if not dur:
        return True
    return last_end <= dur * FIT_TRANSCRIPT_SLACK and dur <= last_end * FIT_MEDIA_MAX_RATIO


# ----------------------------------------------------------------------
# How many voices are in the room.
# ----------------------------------------------------------------------
def speaker_count(utterances):
    """Distinct speakers across the diarised turns (AssemblyAI codes "A", "B").
    Reads utterances, never words[].speaker: the word-level speaker id is a
    derived integer that is null whenever kh-studio had no diarisation, so
    counting it would report 0 or 1 voices for a two-hander. Returns 0 when
    there are no usable turns, and the worker reports THAT as an info finding
    rather than as a speaker mismatch. Pure."""
    speakers = set()
    for u in (utterances or []):
        if not isinstance(u, dict):
            continue
        code = u.get("speaker")
        if code is None or (isinstance(code, str) and not code.strip()):
            continue
        speakers.add(str(code).strip())
    return len(speakers)
