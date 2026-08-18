"""
Stage 2: detect — find the clip-worthy moments.

This is the BRAIN of KH Clipper, and the reason to build our own instead of
paying Opus. Opus scores clips on a generic virality model. We score them on
KH's PROVEN formula from the channel audit: specific story + implied outcome,
told in a real human voice, with a fast emotional payoff.

Trauma-informed by design (embedded, not bolted on): selection leads with agency
not tragedy, protects dignity, honours all paths, and runs every clip through a
safety gate (ok / review / exclude) for a producer to approve before publish.
The heuristic does a light pass; the Grok judgment pass (rerank.py) does the
nuance and can veto a viral-but-undignified moment.

Input:  output/<id>.transcript.json   (from Stage 1 — has word-level timings)
Output: output/<id>.clips.json        (ranked clip specs for Stages 3-6)

Run it standalone to test:
    python src/detect.py output/rMCHK_ky1Rw.transcript.json

Standard library only. No new installs. Tunable via the CONFIG block below.
"""

import json
import re
import sys
from pathlib import Path

# Guardrails are the single source of truth for trauma-informed values.
try:
    from . import guardrails              # imported as a package
except ImportError:
    import guardrails                     # run as a script


# ======================================================================
# CONFIG — tune the behaviour here
# ======================================================================

MIN_LEN_SEC = 8        # nothing shorter than this
MAX_LEN_SEC = 35       # HARD CEILING for social/Shorts/Reels — never exceed 35s.
                       # (length scoring below still rewards shorter; this is the cap.)
PAYOFF_WINDOW_SEC = 4  # the charged hook must land within this many secs (the 4s window)
TOP_N = 5              # how many final clips to surface (KH: exactly 5 finished Shorts)
SHORTLIST_N = 30       # how many heuristic candidates to hand Grok to judge
LOOPABLE_RANK_BONUS = 8  # final-ordering reward for a seamless loop; loops have
                         # counted as views since March 2025 (raised from 4)

# Archetypes (length bands from the audit)
ARCHETYPES = [
    # name,            min, max
    ("Universal Truth", 8, 12),
    ("Moment",          9, 15),
    ("Story Teaser",   16, 35),
]

# ----------------------------------------------------------------------
# Clip type profiles (KH-CTP-001): type steers SELECTION and RANKING only.
# It never weakens the specific-story gate, the cliche penalty, the sensation
# penalty, the safety gate or NMS; those run identically for every type.
# Every typed tunable lives HERE (no magic numbers in the scoring code).
#
# Per profile:
#   weights        multipliers on the existing KH Fit Score terms
#                  (hook, hook4s, turn, keyword, length, agency). "best" is all
#                  1.0, which keeps its scoring bit-identical to the legacy path.
#   target_band    (lo, hi) seconds the type aims for; None = the legacy
#                  shorter-is-better curve ("best" only). Bands come from the
#                  KH-CTP-001 length policy (KH channel evidence, 2026-08-05).
#   allowed_band   (lo, hi) seconds; outside it a candidate is REJECTED for this
#                  type. Always inside the global 8s..35s hard limits.
#   marker_bonus   {"turn"|"agency"|"emotion": (points_per_hit, cap)} extra
#                  additive boost from the existing marker sets.
#   require_lead_with  hard constraint on the lead: "agency" for raw_moment
#                  (only moments where the person acts/decides/mends qualify).
#   tragedy_lead_penalty  points off when the moment leads with tragedy
#                  (hero_today: penalise past-tense tragedy leads).
#   proper_noun_density   (max_per_10_tokens, penalty): universal_truth wants a
#                  plainly-said insight, not a names-heavy anecdote.
#   curiosity_bonus  points added when the hook opens a genuine curiosity gap
#                  (a question, or a pivot marker in the hook): story_teaser.
# ----------------------------------------------------------------------
TYPED_LENGTH_TARGET_SCORE = 10.0   # length score inside the type's target band
TYPED_LENGTH_EDGE_SCORE = 2.0      # length score at the allowed-band edge

_NEUTRAL_WEIGHTS = {"hook": 1.0, "hook4s": 1.0, "turn": 1.0,
                    "keyword": 1.0, "length": 1.0, "agency": 1.0}

TYPE_PROFILES = {
    # Current behaviour, untouched: legacy curve, no boosts, no typed penalties.
    "best": {
        "label": "Best overall",
        "weights": dict(_NEUTRAL_WEIGHTS),
        "target_band": None,
        "allowed_band": (MIN_LEN_SEC, MAX_LEN_SEC),
        "marker_bonus": {},
        "require_lead_with": None,
        "tragedy_lead_penalty": 0,
        "proper_noun_density": None,
        "curiosity_bonus": 0,
    },
    # GOLDEN JOINERY, the mend: the decision, realisation, who showed up.
    "turning_point": {
        "label": "Turning Point",
        "weights": {**_NEUTRAL_WEIGHTS, "turn": 1.6, "agency": 1.4},
        "target_band": (15, 25),
        "allowed_band": (12, 30),
        "marker_bonus": {"turn": (2, 8), "agency": (1, 4)},
        "require_lead_with": None,
        "tragedy_lead_penalty": 0,
        "proper_noun_density": None,
        "curiosity_bonus": 0,
    },
    # HERO TODAY: who they are now, what they built, their message.
    "hero_today": {
        "label": "Hero Today",
        "weights": {**_NEUTRAL_WEIGHTS, "agency": 1.6},
        "target_band": (12, 22),
        "allowed_band": (10, 26),
        "marker_bonus": {"agency": (2, 8)},
        "require_lead_with": None,
        "tragedy_lead_penalty": 12,
        "proper_noun_density": None,
        "curiosity_bonus": 0,
    },
    # FRACTURE, held with care: a vivid honest moment from the breaking point.
    # HARD CONSTRAINT: only lead_with = agency qualifies (fracture as context,
    # never spectacle). The sensation penalty and safety gate run unchanged.
    "raw_moment": {
        "label": "Raw Moment",
        "weights": {**_NEUTRAL_WEIGHTS, "hook4s": 1.2, "turn": 1.2},
        "target_band": (12, 20),
        "allowed_band": (10, 24),
        "marker_bonus": {"emotion": (2, 8)},
        "require_lead_with": "agency",
        "tragedy_lead_penalty": 0,
        "proper_noun_density": None,
        "curiosity_bonus": 0,
    },
    # The wisdom the arc produced: a hard-won insight said plainly. Prefers the
    # short band and low proper-noun density (an insight, not a names-heavy
    # anecdote). The full cliche penalty stays, unchanged.
    "universal_truth": {
        "label": "Universal Truth",
        "weights": {**_NEUTRAL_WEIGHTS, "hook": 1.2},
        "target_band": (10, 15),
        "allowed_band": (8, 16),
        "marker_bonus": {},
        "require_lead_with": None,
        "tragedy_lead_penalty": 0,
        "proper_noun_density": (1.5, 6),   # >1.5 proper nouns per 10 tokens: -6
        "curiosity_bonus": 0,
    },
    # The arc in miniature: a genuine curiosity gap the FULL episode pays off.
    # Must still satisfy on its own as a complete thought (existing rule).
    "story_teaser": {
        "label": "Story Teaser",
        "weights": {**_NEUTRAL_WEIGHTS, "hook": 1.3, "hook4s": 1.2},
        "target_band": (20, 30),
        "allowed_band": (16, 35),
        "marker_bonus": {},
        "require_lead_with": None,
        "tragedy_lead_penalty": 0,
        "proper_noun_density": None,
        "curiosity_bonus": 6,
    },
}

# ----------------------------------------------------------------------
# KH-AUD-001: standalone longer-form audiogram duration presets (worker
# action="audiogram"). Each preset's `band_override` widens candidate-window
# building + length scoring for a `detect()` call WELL past the Shorts 35s
# ceiling — every other TYPE_PROFILES tunable (weights, markers, hard
# constraints) still applies unchanged for whichever clip_type lens is picked.
# target_band aims near the top of the preset (a full-length audiogram, not a
# padded-out short one); allowed_band gives room below when nothing in the
# episode cleanly reaches the target length.
AUDIOGRAM_DURATION_PRESETS = {
    30:  {"target_band": (24, 30), "allowed_band": (18, 30)},
    60:  {"target_band": (48, 60), "allowed_band": (35, 60)},
    90:  {"target_band": (72, 90), "allowed_band": (55, 90)},
    120: {"target_band": (95, 120), "allowed_band": (75, 120)},
}


def audiogram_band_override(duration_sec):
    """(target_band, allowed_band) for a KH-AUD-001 duration preset, or None for
    an unrecognised value (caller should reject/default rather than guess)."""
    preset = AUDIOGRAM_DURATION_PRESETS.get(duration_sec)
    return (preset["target_band"], preset["allowed_band"]) if preset else None


def typed_length_score(length_sec, profile):
    """Length score for a TYPED job: best inside the target band, degrading
    linearly toward the allowed-band edges, None (reject) outside the allowed
    band. `best` never comes here (its target_band is None -> legacy curve)."""
    t_lo, t_hi = profile["target_band"]
    a_lo, a_hi = profile["allowed_band"]
    if length_sec < a_lo or length_sec > a_hi:
        return None
    if t_lo <= length_sec <= t_hi:
        return TYPED_LENGTH_TARGET_SCORE
    if length_sec < t_lo:
        span, dist = t_lo - a_lo, t_lo - length_sec
    else:
        span, dist = a_hi - t_hi, length_sec - t_hi
    if span <= 0:
        return TYPED_LENGTH_TARGET_SCORE
    frac = min(1.0, dist / span)
    return round(TYPED_LENGTH_TARGET_SCORE
                 - (TYPED_LENGTH_TARGET_SCORE - TYPED_LENGTH_EDGE_SCORE) * frac, 1)

# The niche keyword bank, grouped by theme. We match on theme words because
# guests rarely say the exact SEO phrase, but they say the theme.
KEYWORD_THEMES = {
    # tier 1 (weighted higher)
    "trauma healing":            (1.3, ["trauma", "traumatic", "ptsd", "flashback", "triggered", "wound", "healing", "heal", "healed"]),
    "toxic family":              (1.3, ["family", "mother", "mum", "father", "dad", "parents", "brother", "sister", "household", "childhood"]),
    "self worth":                (1.3, ["worth", "worthless", "enough", "confidence", "self-esteem", "value", "deserve", "shame", "ashamed"]),
    "healing journey":           (1.3, ["recovery", "recover", "growth", "rebuild", "rebuilt", "survived", "survive", "overcome", "stronger"]),
    "mental health":             (1.3, ["depression", "depressed", "anxiety", "anxious", "therapy", "therapist", "mental", "breakdown"]),
    # tier 2
    "workplace bullying":        (1.0, ["work", "boss", "manager", "colleague", "bully", "bullied", "bullying", "office", "job", "workplace"]),
    "narcissistic abuse":        (1.0, ["narcissist", "narcissistic", "manipulation", "manipulate", "gaslight", "gaslit", "control", "controlling"]),
    "addiction recovery":        (1.0, ["addiction", "addict", "addicted", "alcohol", "alcoholic", "drinking", "drugs", "drug", "cocaine", "sober", "sobriety", "relapse", "overdose"]),
    "childhood trauma":          (1.0, ["abuse", "abused", "neglect", "neglected", "hit", "beaten", "molested"]),
    "grief and loss":            (1.0, ["grief", "grieving", "died", "death", "loss", "lost", "funeral", "cancer", "diagnosis"]),
}

# Emotional weight — words that signal a real feeling, not advice.
EMOTION_WORDS = {
    "afraid", "scared", "terrified", "fear", "ashamed", "shame", "alone", "lonely",
    "broken", "lost", "hopeless", "helpless", "cried", "crying", "tears", "pain",
    "hurt", "hurting", "angry", "rage", "guilt", "guilty", "love", "loved", "forgive",
    "forgiveness", "desperate", "numb", "empty", "panic", "trapped", "suicidal",
    "darkness", "dark", "silence", "screaming", "begged",
}

# Turn / reveal markers — the pivot in a story. Audit: the payoff is the click.
TURN_MARKERS = {
    "but", "until", "then", "suddenly", "realised", "realized", "realise", "realize",
    "changed", "finally", "no longer", "used to", "decided", "chose",
}

# Motivational cliché — punished HARD when it stands alone with no story.
# The audit proved these die (300 views vs 1,100+).
CLICHE_PHRASES = [
    "you deserve", "you are enough", "you're enough", "never give up", "stay strong",
    "it gets better", "believe in yourself", "you've got this", "be kind to yourself",
    "everything happens for a reason", "your journey", "self-care", "love yourself",
    "you matter", "you are worthy", "trust the process", "good things",
]

# Sensitive disclosures — flag for consent before publish (the Reject gate).
# Single source of truth lives in guardrails.py (KH-TIC-001 §3.1 / §5.3).
CONSENT_TRIGGERS = guardrails.CONSENT_TRIGGERS

# ----------------------------------------------------------------------
# TRAUMA-INFORMED LAYER (embedded into selection, not just flagged).
# KH value: lead with agency, not tragedy. Protect dignity. Honour all paths.
# The heuristic does a light pass; the Grok judgment pass (rerank.py) does the
# real nuance. Both feed a producer review gate before anything publishes.
# ----------------------------------------------------------------------

# Agency / mending language — the person acting, deciding, understanding, healing.
# We REWARD these so dignified "mending" moments rank above raw "breaking" ones.
AGENCY_WORDS = {
    "decided", "chose", "choose", "realised", "realized", "learned", "learnt",
    "started", "stopped", "asked", "rebuilt", "rebuild", "forgave", "forgive",
    "changed", "chose", "found", "built", "grew", "took", "walked", "left",
    "understood", "accepted", "control", "decision", "responsibility",
}

# Sensational / "inspiration porn" framing — lead-with-tragedy. Gently penalised.
SENSATION_MARKERS = {
    "despite everything", "against all odds", "inspiration", "inspiring",
    "you won't believe", "shocking", "miracle", "broke down in tears",
}


def assess_safety(text):
    """Return (safety, note) for the producer review gate.
    'ok' = fine, 'review' = a producer must look first. The Grok pass can also
    return 'exclude'; the heuristic never excludes on its own, it flags for review."""
    low = text.lower()
    hits = [t for t in CONSENT_TRIGGERS if t in low]
    if hits:
        return "review", f"sensitive disclosure ({', '.join(sorted(set(hits))[:3])}) — producer check"
    return "ok", ""

# A clip must START on a clean, self-contained line — never a mid-conversation
# fragment. The audit's #1 rule: a complete thought, not "But, yeah, I...".
BAD_OPENERS = {
    "but", "so", "and", "because", "cause", "'cause", "or", "yeah", "yep", "oh",
    "um", "uh", "well", "also", "then", "plus", "anyway", "actually", "like",
    "okay", "ok", "right", "now", "hmm", "nah", "no,", "yes,", "i mean", "you know",
}

# Lines that are the HOST asking a question, not the guest telling their story.
# The guest's answers are the content, so we don't open a clip on these.
INTERVIEWER_OPENERS = (
    "can you", "could you", "would you", "what was", "what's your", "what is your",
    "how did you", "how do you", "tell me", "do you", "did you", "have you",
    "when did you", "where did you", "why did you", "what made you", "talk to me",
    "paint a picture", "walk me through", "let's talk",
)


def is_good_opener(sentence):
    """True only if this line is a clean, self-contained hook opener spoken
    by the guest (not the host). Heuristic until diarization is wired in."""
    text = sentence["text"].strip()
    low = text.lower()
    words = _tokens(low)
    if sentence["n_words"] < 4:
        return False
    if sentence["n_words"] > 22:          # run-on fragment, not a punchy hook
        return False
    if words and words[0] in BAD_OPENERS:
        return False
    if any(low.startswith(p) for p in INTERVIEWER_OPENERS):
        return False
    if low.startswith(("but ", "so ", "and ", "because ", "well ", "yeah", "oh ")):
        return False
    # Must start with a capital — a lowercase start is a mid-sentence fragment,
    # not a clean, self-contained thought.
    if not text[:1].isupper():
        return False
    # Meta / filler that isn't story content.
    META_FILLER = ("forgot the question", "off track", "what was the question",
                   "where was i", "i lost my train", "sorry,", "i'm rambling")
    if any(p in low for p in META_FILLER):
        return False
    # Host-steering lines.
    if low.startswith(("let's", "let me", "first,", "tell us", "talk us")):
        return False
    # Addressing the guest by name: "Rick, ..." (Capitalised word + comma start).
    if re.match(r"^[A-Z][a-z]+,", text):
        return False
    # Second-person with no first-person = the host asking/summarising.
    second = sum(1 for t in words if t in ("you", "your", "you're", "you've"))
    first = sum(1 for t in words if t in ("i", "me", "my", "we", "us", "our"))
    if second >= 1 and first == 0:
        return False
    return True


# ======================================================================
# Build sentences from the flat word list
# ======================================================================

def build_sentences(words):
    """Group word-objects into sentences using terminal punctuation."""
    sentences, cur = [], []
    for w in words:
        cur.append(w)
        if re.search(r"[.!?]$", w["text"].strip()):
            sentences.append(_make_sentence(cur))
            cur = []
    if cur:
        sentences.append(_make_sentence(cur))
    return [s for s in sentences if s["n_words"] >= 2]


def _make_sentence(word_objs):
    text = " ".join(w["text"] for w in word_objs).strip()
    # Sentence speaker = the speaker who said most of its words (if labelled).
    speakers = [w.get("speaker") for w in word_objs if w.get("speaker") is not None]
    speaker = max(set(speakers), key=speakers.count) if speakers else None
    return {
        "text": text,
        "start": word_objs[0]["start"],
        "end": word_objs[-1]["end"],
        "words": word_objs,
        "n_words": len(word_objs),
        "speaker": speaker,
    }


def identify_guest(words):
    """The guest is the speaker who talks most (the host asks, the guest tells).
    Returns None if the transcript has no speaker labels (old/non-diarized)."""
    counts = {}
    for w in words:
        sp = w.get("speaker")
        if sp is not None:
            counts[sp] = counts.get(sp, 0) + 1
    return max(counts, key=counts.get) if counts else None


# ======================================================================
# Build candidate clips (windows of consecutive sentences)
# ======================================================================

def trim_opener(sentence):
    """Cold-open guarantee: the clip's first spoken words must BE the hook line.
    Snap the start past any leading filler (um/so/yeah/and/...) using the word-level
    timings, so there's no throat-clear or dead air before the first content word.
    Reuses BAD_OPENERS to TRIM, not just reject. Never strips more than the first few
    tokens, and never guts a short line."""
    words = sentence.get("words") or []
    if len(words) < 5:
        return sentence
    keep_from = 0
    for k, w in enumerate(words[:3]):     # only ever trim the first few tokens
        tok = re.sub(r"[^a-z']", "", w["text"].lower())
        if tok and tok in BAD_OPENERS:
            keep_from = k + 1
        else:
            break
    if keep_from == 0 or keep_from >= len(words) - 2:
        return sentence                   # nothing to trim, or would gut the line
    return _make_sentence(words[keep_from:])


def build_candidates(sentences, guest_speaker=None, min_len_sec=None, max_len_sec=None):
    """min_len_sec/max_len_sec default to the Shorts hard limits (MIN_LEN_SEC/
    MAX_LEN_SEC) — pass wider values ONLY for a non-Shorts caller (KH-AUD-001's
    longer-form audiogram job); every existing caller is unaffected."""
    lo = MIN_LEN_SEC if min_len_sec is None else min_len_sec
    hi = MAX_LEN_SEC if max_len_sec is None else max_len_sec
    candidates = []
    for i in range(len(sentences)):
        # If we know who the guest is, only open clips on the guest's lines.
        if guest_speaker is not None and sentences[i].get("speaker") != guest_speaker:
            continue
        opener = trim_opener(sentences[i])   # cold-open: snap to first content word
        if not is_good_opener(opener):
            continue                      # only open clips on clean hook lines
        start = opener["start"]
        for j in range(i, len(sentences)):
            end = sentences[j]["end"]
            dur = end - start
            if dur < lo:
                continue
            if dur > hi:
                break
            # Use the trimmed opener as sentence[0] so the hook line, the start time
            # and the 4s window all begin on the guest's first real word.
            window = [opener] + sentences[i + 1:j + 1]
            candidates.append({
                "start": start,
                "end": end,
                "length_sec": round(dur, 1),
                "sentences": window,
            })
    return candidates


# ======================================================================
# Scoring — the KH Fit Score
# ======================================================================

def _tokens(text):
    return re.findall(r"[a-z']+", text.lower())


# Audio-emotion bonus ceiling. The bonus (src/emotion.py) is additive and
# capped BELOW the off-theme penalty (10) and far below the safety machinery,
# so an emotional delivery can nudge ranking but never overcome a values or
# relevance rejection. A bonus, never a gate.
EMOTION_BONUS_CAP = 8.0


def score_candidate(c, clip_type="best", band_override=None, emotion_bonus=0.0):
    """emotion_bonus: optional precomputed audio-emotion bonus (0..8, from
    src/emotion.py) for this candidate's window; clamped to EMOTION_BONUS_CAP
    and ADDED to the total. 0.0 (default) keeps scoring byte-identical to the
    transcript-only path.
    band_override — optional (target_band, allowed_band) pair that REPLACES the
    type profile's own bands for the length-scoring step only (everything else
    about the type's lens — weights, markers, hard constraints — is unchanged).
    None (default) = the type's own Shorts-tuned bands, unchanged behaviour.
    Used by the standalone longer-form audiogram job (KH-AUD-001) so a clip_type
    lens like `turning_point` can still be used at 60-120s instead of being
    rejected outright by its 12-30s Shorts allowed_band. `best`'s legacy curve
    (target_band is None) is likewise overridden to a target-band curve when
    band_override is given, since the short-is-better curve isn't meaningful at
    a multi-minute length."""
    profile = TYPE_PROFILES.get(clip_type) or TYPE_PROFILES["best"]
    if band_override is not None:
        profile = dict(profile)
        profile["target_band"], profile["allowed_band"] = band_override
    text = " ".join(s["text"] for s in c["sentences"])
    low = text.lower()
    toks = _tokens(text)
    tokset = set(toks)
    hook = c["sentences"][0]["text"]
    hook_low = hook.lower()

    breakdown = {}

    # 1. Specific-story gate (pass/fail). First person + concrete detail.
    first_person = sum(1 for t in toks if t in ("i", "me", "my", "we", "us", "our"))
    has_number = bool(re.search(r"\b\d+\b", text)) or any(
        t in tokset for t in ("year", "years", "day", "days", "old", "first", "last")
    )
    proper_nouns = len(re.findall(r"(?<!^)(?<![.!?]\s)\b[A-Z][a-z]+", text))
    is_story = first_person >= 1 and (has_number or proper_nouns >= 1 or first_person >= 3)
    if not is_story:
        return None  # rejected — not a specific story from a real person

    # 2. Hook strength — complete thought, contrast, emotional pull.
    # (Fragment/interviewer openers are already filtered out upstream.)
    hook_toks = _tokens(hook)
    hook_score = 2                            # baseline: it's a clean opener
    if re.search(r"[.!?]$", hook.strip()):
        hook_score += 1                       # is a complete sentence
    if any(m in hook_low.split() for m in TURN_MARKERS):
        hook_score += 3                       # contains a pivot
    if any(e in hook_toks for e in EMOTION_WORDS):
        hook_score += 3                       # emotional pull in the opener
    if hook.strip().endswith("?"):
        hook_score += 1                       # a curiosity question (guest's own)
    if 4 <= c["sentences"][0]["n_words"] <= 14:
        hook_score += 1                       # punchy, not rambling
    breakdown["hook"] = min(hook_score, 10)

    # 3. hook4s — a complete, charged hook must land within the first 4 seconds.
    # (PAYOFF_WINDOW_SEC = 4.) This is the engine: win the first 4 seconds.
    payoff_cutoff = c["start"] + PAYOFF_WINDOW_SEC
    early_words = [w["text"].lower() for s in c["sentences"] for w in s["words"]
                   if w["start"] <= payoff_cutoff]
    early_blob = " ".join(early_words)
    hook4s = 0
    if any(e in early_blob for e in EMOTION_WORDS):
        hook4s += 6
    if any(m in early_blob for m in TURN_MARKERS):
        hook4s += 4
    if re.search(r"\b\d+\b", early_blob):     # a number inside the 4s lands fast
        hook4s += 2
    breakdown["hook4s"] = min(hook4s, 10)

    # 4. Emotional turn across the clip.
    emo_hits = sum(1 for e in EMOTION_WORDS if e in low)
    turn_hits = sum(1 for m in TURN_MARKERS if m in low)
    breakdown["turn"] = min(emo_hits * 2 + turn_hits * 2, 10)

    # 5/7. Keyword/theme match.
    best_kw, kw_weighted = None, 0.0
    for kw, (weight, words_) in KEYWORD_THEMES.items():
        hits = sum(1 for word in words_ if word in low)
        if hits:
            weighted = hits * weight
            if weighted > kw_weighted:
                kw_weighted, best_kw = weighted, kw
    breakdown["keyword"] = min(int(kw_weighted * 2), 10)

    # 6. Length discipline. "best" keeps the legacy shorter-is-better curve
    # (bit-identical); a typed job scores distance from ITS target band and
    # rejects outside its allowed band (KH-CTP-001 length policy). The global
    # 8s..35s hard limits still bound every candidate upstream.
    if profile["target_band"] is None:
        breakdown["length"] = round(max(0, 10 - (c["length_sec"] - MIN_LEN_SEC) * 0.4), 1)
    else:
        _len_score = typed_length_score(c["length_sec"], profile)
        if _len_score is None:
            return None  # outside this type's allowed band
        breakdown["length"] = _len_score

    # Cliché penalty — kills motivational-only clips.
    cliche_hits = sum(1 for p in CLICHE_PHRASES if p in low)
    cliche_penalty = cliche_hits * 4
    # ...but forgive it if wrapped in a real story (emotion present).
    if emo_hits >= 2:
        cliche_penalty = max(0, cliche_penalty - 4)

    # Penalise clips that are neither on-theme nor emotional — they're filler.
    off_theme_penalty = 0
    if breakdown["keyword"] == 0 and emo_hits == 0:
        off_theme_penalty = 10

    # TRAUMA-INFORMED: lead with agency, not tragedy. Reward moments where the
    # person acts/decides/mends; gently penalise sensational, inspiration-porn framing.
    agency_hits = sum(1 for a in AGENCY_WORDS if a in tokset)
    dignity_bonus = min(agency_hits * 2, 6)
    breakdown["agency"] = dignity_bonus
    sensation_penalty = sum(4 for p in SENSATION_MARKERS if p in low)
    lead_with = "agency" if agency_hits >= 1 else "tragedy"

    # KH-CTP-001 hard constraint: raw_moment only qualifies when the person is
    # acting/deciding/mending (lead_with = agency). Type never weakens safety;
    # this only ever REMOVES candidates from a typed job.
    if profile["require_lead_with"] and lead_with != profile["require_lead_with"]:
        return None

    # Typed marker boosts + penalties (all tunables live in TYPE_PROFILES).
    # "best" has none of these, so its total is the exact legacy arithmetic.
    _marker_hits = {"turn": turn_hits, "agency": agency_hits, "emotion": emo_hits}
    type_bonus = sum(min(per_hit * _marker_hits.get(mk, 0), cap)
                     for mk, (per_hit, cap) in profile["marker_bonus"].items())
    type_penalty = 0
    if lead_with == "tragedy":
        type_penalty += profile["tragedy_lead_penalty"]
    _pnd = profile["proper_noun_density"]
    if _pnd and toks:
        if proper_nouns * 10.0 / len(toks) > _pnd[0]:
            type_penalty += _pnd[1]
    if profile["curiosity_bonus"]:
        if hook.strip().endswith("?") or any(m in hook_low.split() for m in TURN_MARKERS):
            type_bonus += profile["curiosity_bonus"]

    # Audio-emotion bonus (src/emotion.py): additive only, clamped so it can
    # never outweigh the safety/off-theme penalties. Zero when no audio ran.
    _emo = min(max(float(emotion_bonus or 0.0), 0.0), EMOTION_BONUS_CAP)
    if _emo > 0:
        breakdown["emotion_audio"] = round(_emo, 1)

    w = profile["weights"]
    total = (
        breakdown["hook"] * 2.0 * w["hook"]
        + breakdown["hook4s"] * 2.5 * w["hook4s"]
        + breakdown["turn"] * 1.5 * w["turn"]
        + breakdown["keyword"] * 2.2 * w["keyword"]
        + breakdown["length"] * 1.0 * w["length"]
        + dignity_bonus * w["agency"]
        + type_bonus
        + _emo
        - cliche_penalty
        - off_theme_penalty
        - sensation_penalty
        - type_penalty
    )
    # normalise to roughly 0-100
    fit = max(0, min(100, round(total * 1.05)))

    # Archetype by length.
    archetype = "Story Teaser"
    for name, lo, hi in ARCHETYPES:
        if lo <= c["length_sec"] <= hi:
            archetype = name
            break

    # highlight_word — strongest theme/emotion token in the hook.
    highlight = _pick_highlight(hook)

    # Trauma-informed safety gate (producer reviews before publish).
    safety, safety_note = assess_safety(text)
    consent_required = safety != "ok"

    return {
        "start": round(c["start"], 2),
        "end": round(c["end"], 2),
        "length_sec": c["length_sec"],
        "clip_type": clip_type,            # the lens this candidate was scored under
        "archetype": archetype,
        "hook_line": hook,
        "matched_keyword": best_kw,
        "highlight_word": highlight,
        "fit_score": fit,
        "hook4s": breakdown["hook4s"],     # the 4-second hook score, surfaced
        "hook_formula": "none",            # Grok labels this in the judgment pass
        "loopable": False,                 # Grok flags this in the judgment pass
        "score_breakdown": breakdown,
        "lead_with": lead_with,
        "safety": safety,
        "safety_note": safety_note,
        "consent_required": consent_required,
        "consent_ok": False,
        "text": text,
    }


def _pick_highlight(hook):
    for t in _tokens(hook):
        if t in EMOTION_WORDS:
            return t
    for kw, (w, words_) in KEYWORD_THEMES.items():
        for word in words_:
            if word in hook.lower():
                return word
    # fall back to the longest word in the hook
    toks = _tokens(hook)
    return max(toks, key=len) if toks else ""


# ======================================================================
# Non-maximal suppression — drop overlapping clips, keep the best.
# ======================================================================

def suppress_overlaps(clips, max_overlap=0.5):
    kept = []
    for c in sorted(clips, key=lambda x: x["fit_score"], reverse=True):
        overlap = False
        for k in kept:
            lo = max(c["start"], k["start"])
            hi = min(c["end"], k["end"])
            inter = max(0, hi - lo)
            shorter = min(c["end"] - c["start"], k["end"] - k["start"])
            if shorter > 0 and inter / shorter > max_overlap:
                overlap = True
                break
        if not overlap:
            kept.append(c)
    return kept


# ======================================================================
# Public entry point
# ======================================================================

def _emotion_bonuses(candidates, audio_path):
    """Per-candidate audio-emotion bonuses (src/emotion.py), or None when the
    audio signal is unavailable for any reason. Best-effort by design: a
    missing file, missing ffmpeg or missing numpy silently yields None and the
    caller scores transcript-only, exactly as today."""
    if not audio_path or not candidates:
        return None
    try:
        try:
            from . import emotion             # imported as a package
        except ImportError:
            import emotion                    # run as a script
        pcm = emotion.load_pcm(audio_path)
        if pcm is None:
            return None
        median = emotion.file_median_rms(pcm, emotion.SAMPLE_RATE)
        bonuses = []
        for c in candidates:
            cand_words = [w for s in c["sentences"] for w in s["words"]]
            feats = emotion.window_features(
                pcm, emotion.SAMPLE_RATE, c["start"], c["end"],
                words=cand_words, file_median=median)
            bonuses.append(emotion.emotion_bonus(feats))
        return bonuses
    except Exception:
        return None


def detect(transcript_path, use_llm=True, top_n=TOP_N, usage_ctx=None,
           clip_type="best", reviewer_anchors=None, band_override=None,
           audio_path=None):
    """`clip_type` (KH-CTP-001) picks the selection lens from TYPE_PROFILES;
    "best" is bit-identical to the legacy scoring. `reviewer_anchors` is an
    optional list of Episode Reviewer evidence quotes, passed to the Grok
    judgment pass as ADVISORY anchors (never commands). `band_override`
    (KH-AUD-001) — optional (target_band, allowed_band) pair; when given, both
    candidate-window building and scoring use it instead of the Shorts 8-35s
    limits and the type's own Shorts-tuned band. None (default) = unchanged
    Shorts behaviour. `audio_path`: optional path to the episode audio (the
    transcription wav); when given, each candidate window earns a small
    audio-emotion bonus (src/emotion.py, capped at EMOTION_BONUS_CAP, a bonus
    never a gate). None (default) = byte-identical transcript-only scoring."""
    if clip_type not in TYPE_PROFILES:
        clip_type = "best"                 # unknown type degrades to current behaviour
    data = json.loads(Path(transcript_path).read_text())
    words = data["words"]
    guest_speaker = identify_guest(words)
    sentences = build_sentences(words)
    min_len, max_len = (None, None) if band_override is None else (
        band_override[1][0], band_override[1][1])
    candidates = build_candidates(sentences, guest_speaker,
                                  min_len_sec=min_len, max_len_sec=max_len)

    emo_bonuses = _emotion_bonuses(candidates, audio_path)
    scored = [s for s in (score_candidate(c, clip_type=clip_type, band_override=band_override,
                                          emotion_bonus=(emo_bonuses[i] if emo_bonuses else 0.0))
                          for i, c in enumerate(candidates)) if s]
    deduped = suppress_overlaps(scored)
    heuristic_ranked = sorted(deduped, key=lambda x: x["fit_score"], reverse=True)

    method = "heuristic"
    llm_error = None
    top = heuristic_ranked[:top_n]

    # Stage 2.5 — hand the shortlist to Grok for the judgment pass.
    if use_llm and heuristic_ranked:
        shortlist = heuristic_ranked[:SHORTLIST_N]
        for i, c in enumerate(shortlist):
            c["index"] = i
        try:
            try:
                from . import rerank as rr      # imported as a package
            except ImportError:
                import rerank as rr              # run as a script
            picks = rr.rerank(shortlist, data.get("title", ""), top_n=top_n, usage_ctx=usage_ctx,
                              clip_type=clip_type, reviewer_anchors=reviewer_anchors)
            chosen = []
            for p in picks:
                idx = p.get("index")
                if idx is None or not (0 <= idx < len(shortlist)):
                    continue
                # KH values override clip-worthiness: drop anything Grok excludes.
                safety = (p.get("safety") or "").lower()
                if safety == "exclude":
                    continue
                base = dict(shortlist[idx])
                base["hook_line"] = p.get("hook") or base["hook_line"]
                if p.get("archetype"):
                    base["archetype"] = p["archetype"]
                base["why"] = p.get("why", "")
                base["fit_score"] = p.get("score", base["fit_score"])
                if p.get("lead_with"):
                    base["lead_with"] = p["lead_with"]
                # 4-second hook engine metadata from the judgment pass.
                hf = (p.get("hook_formula") or "none")
                base["hook_formula"] = hf if hf in guardrails.HOOK_FORMULA_KEYS else "none"
                base["loopable"] = bool(p.get("loopable", False))
                # Grok's safety read wins over the heuristic's (it has the nuance).
                if safety in ("ok", "review"):
                    base["safety"] = safety
                    base["safety_note"] = p.get("safety_note", base.get("safety_note", ""))
                base["consent_required"] = base.get("safety", "ok") != "ok"
                base["scored_by"] = "grok"
                base.pop("index", None)
                chosen.append(base)
            if chosen:
                # Reward loopable clips in the final ranking (a clip that replays
                # seamlessly earns more watch time, and loops have counted as
                # views since March 2025, so the weight is +8, raised from +4).
                # Tiebreak only. Never inflates the stored fit_score, and never
                # fabricates a loop.
                chosen.sort(
                    key=lambda c: c.get("fit_score", 0)
                    + (LOOPABLE_RANK_BONUS if c.get("loopable") else 0),
                    reverse=True,
                )
                top = chosen[:top_n]
                method = "grok"
        except Exception as e:
            llm_error = str(e)
            method = "heuristic (Grok pass failed)"

    for n, clip in enumerate(top, 1):
        clip["clip_id"] = f"{data.get('id', 'clip')}-{n:02d}"
        clip["source_video_id"] = data.get("id")
        clip.pop("index", None)

    # Candidate pool for the per-clip "replace" action: the deduped, ranked moments
    # BEYOND the shipped top_n. Persisted by the worker so a later "replace" can pick a
    # genuinely different unused moment without re-fetching/re-transcribing. Light fields
    # only (enough to cut + caption + write a fresh metadata pack).
    POOL_FIELDS = ("start", "end", "length_sec", "clip_type", "archetype", "hook_line",
                   "highlight_word", "fit_score", "lead_with", "safety",
                   "safety_note", "text")
    candidate_pool = [{k: c.get(k) for k in POOL_FIELDS} for c in heuristic_ranked[:40]]

    return {
        "source": data.get("id"),
        "title": data.get("title"),
        "clip_type": clip_type,
        "method": method,
        "llm_error": llm_error,
        "diarized": guest_speaker is not None,
        "guest_speaker": guest_speaker,
        "n_candidates": len(candidates),
        "n_passed_gate": len(scored),
        "n_requested": top_n,
        "clips": top,
        "candidate_pool": candidate_pool,
    }


def _fmt(t):
    m, s = divmod(int(t), 60)
    return f"{m}:{s:02d}"


def _print_report(result):
    print(f"\n[detect] {result['title']}")
    if result.get("clip_type") and result["clip_type"] != "best":
        print(f"[detect] clip type: {result['clip_type']} "
              f"({TYPE_PROFILES[result['clip_type']]['label']})")
    if result.get("diarized"):
        print(f"[detect] speaker labelling ON — guest = speaker {result['guest_speaker']} "
              f"(clips built from guest lines only)")
    else:
        print("[detect] speaker labelling OFF (transcript has no speaker labels) "
              "— using text heuristics")
    if result.get("method") == "grok":
        print("[detect] judged by: Grok (LLM judgment pass)")
    else:
        print(f"[detect] judged by: {result.get('method')}")
        if result.get("llm_error"):
            print(f"[detect]   (Grok note: {result['llm_error']})")
    print(f"[detect] {result['n_candidates']} candidate windows -> "
          f"{result['n_passed_gate']} passed the specific-story gate -> "
          f"top {len(result['clips'])} below\n")
    for n, c in enumerate(result["clips"], 1):
        safety = c.get("safety", "review" if c.get("consent_required") else "ok")
        flag = ""
        if safety == "review":
            flag = f"  [REVIEW: {c.get('safety_note') or 'sensitive'}]"
        elif safety == "exclude":
            flag = "  [EXCLUDE]"
        lead = c.get("lead_with")
        lead_tag = f"  led-with: {lead}" if lead else ""
        hf = c.get("hook_formula", "none")
        hf_tag = f"  formula: {hf}" if hf and hf != "none" else ""
        loop_tag = "  ↻ loopable" if c.get("loopable") else ""
        print(f"#{n}  score {c['fit_score']} (hook4s {c.get('hook4s', '-')})  |  "
              f"{c['archetype']}  |  "
              f"{_fmt(c['start'])}-{_fmt(c['end'])} ({c['length_sec']}s)  |  "
              f"kw: {c['matched_keyword']}{lead_tag}{hf_tag}{loop_tag}{flag}")
        print(f"     HOOK: \"{c['hook_line']}\"")
        if c.get("why"):
            print(f"     WHY:  {c['why']}")
        print()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    use_llm = "--no-llm" not in args
    consumed = set()          # indexes of flag VALUES, so they never read as paths
    count = TOP_N
    if "--count" in args:
        ci = args.index("--count")
        if ci + 1 < len(args):
            count = int(args[ci + 1])
            consumed.add(ci + 1)
    clip_type = "best"
    if "--type" in args:
        ti = args.index("--type")
        if ti + 1 < len(args):
            clip_type = args[ti + 1]
            consumed.add(ti + 1)
        if clip_type not in TYPE_PROFILES:
            print(f"Unknown --type {clip_type!r}. One of: {', '.join(TYPE_PROFILES)}")
            sys.exit(1)
    paths = [a for i, a in enumerate(args)
             if not a.startswith("--") and not a.isdigit() and i not in consumed]
    if not paths:
        print("Usage: python src/detect.py output/<id>.transcript.json "
              "[--no-llm] [--count N] [--type best|turning_point|hero_today|"
              "raw_moment|universal_truth|story_teaser]")
        sys.exit(1)

    tpath = paths[0]
    if clip_type != "best":
        print(f"[detect] clip type lens: {clip_type}")
    if use_llm:
        print("[detect] running Grok judgment pass (use --no-llm to skip)...")
    result = detect(tpath, use_llm=use_llm, top_n=count, clip_type=clip_type)
    _print_report(result)

    out_path = Path(str(tpath).replace(".transcript.json", ".clips.json"))
    out_path.write_text(json.dumps(result, indent=2))
    print(f"[detect] Saved {len(result['clips'])} clip specs to: {out_path}")
