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

# Archetypes (length bands from the audit)
ARCHETYPES = [
    # name,            min, max
    ("Universal Truth", 8, 12),
    ("Moment",          9, 15),
    ("Story Teaser",   16, 35),
]

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


def build_candidates(sentences, guest_speaker=None):
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
            if dur < MIN_LEN_SEC:
                continue
            if dur > MAX_LEN_SEC:
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


def score_candidate(c):
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

    # 6. Length discipline — shorter is better (completion rate).
    breakdown["length"] = round(max(0, 10 - (c["length_sec"] - MIN_LEN_SEC) * 0.4), 1)

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

    total = (
        breakdown["hook"] * 2.0
        + breakdown["hook4s"] * 2.5
        + breakdown["turn"] * 1.5
        + breakdown["keyword"] * 2.2
        + breakdown["length"] * 1.0
        + dignity_bonus
        - cliche_penalty
        - off_theme_penalty
        - sensation_penalty
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

def detect(transcript_path, use_llm=True, top_n=TOP_N):
    data = json.loads(Path(transcript_path).read_text())
    words = data["words"]
    guest_speaker = identify_guest(words)
    sentences = build_sentences(words)
    candidates = build_candidates(sentences, guest_speaker)

    scored = [s for s in (score_candidate(c) for c in candidates) if s]
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
            picks = rr.rerank(shortlist, data.get("title", ""), top_n=top_n)
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
                # seamlessly earns more watch time). Tiebreak only — never inflates
                # the stored fit_score, and never fabricates a loop.
                chosen.sort(
                    key=lambda c: c.get("fit_score", 0) + (4 if c.get("loopable") else 0),
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

    return {
        "source": data.get("id"),
        "title": data.get("title"),
        "method": method,
        "llm_error": llm_error,
        "diarized": guest_speaker is not None,
        "guest_speaker": guest_speaker,
        "n_candidates": len(candidates),
        "n_passed_gate": len(scored),
        "n_requested": top_n,
        "clips": top,
    }


def _fmt(t):
    m, s = divmod(int(t), 60)
    return f"{m}:{s:02d}"


def _print_report(result):
    print(f"\n[detect] {result['title']}")
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
    count = TOP_N
    if "--count" in args:
        ci = args.index("--count")
        if ci + 1 < len(args):
            count = int(args[ci + 1])
    paths = [a for a in args if not a.startswith("--") and not a.isdigit()]
    if not paths:
        print("Usage: python src/detect.py output/<id>.transcript.json [--no-llm] [--count N]")
        sys.exit(1)

    tpath = paths[0]
    if use_llm:
        print("[detect] running Grok judgment pass (use --no-llm to skip)...")
    result = detect(tpath, use_llm=use_llm, top_n=count)
    _print_report(result)

    out_path = Path(str(tpath).replace(".transcript.json", ".clips.json"))
    out_path.write_text(json.dumps(result, indent=2))
    print(f"[detect] Saved {len(result['clips'])} clip specs to: {out_path}")
