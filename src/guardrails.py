"""
KH Clipper — Guardrails (single source of truth for trauma-informed values).

Governance: this file is the committed home of the rules that detect.py, rerank.py
and metadata.py all read. Per KH-TIC-001 (Trauma-Informed Content & AI Governance
Standard) and the migration-drift rule, the values live in ONE place so they cannot
drift apart across modules. Change a rule here and behaviour changes everywhere.

MASTER RULE (overrides everything): trauma-informed values are the guardrail. A
reach / hook / fit score NEVER overrides a values check. This is the two-layer model:
  - Layer 1 (values) runs first and is non-negotiable.
  - Layer 2 (growth optimisation) operates ONLY inside Layer 1.
Every generated output (clip pick, hook, title, description, hashtags, pinned comment)
is produced under growth tactics, THEN validated against Layer 1.

Each rule below is traced to its KH-TIC-001 source so the config is auditable against
the governance standard (see KH-TIC-001-master-content-v1.0-draft.md).

VidIQ is banned. No VidIQ calls, imports, or VidIQ-style title scoring anywhere.
"""

# ----------------------------------------------------------------------
# The 7 locked guardrails (Riverside Co-creator setup), mapped to KH-TIC-001.
# ----------------------------------------------------------------------
GUARDRAILS = [
    # text,                                                                                  TIC source
    ("Never sensationalise, dramatise or exploit a guest's trauma. Protect dignity in every output.", "TIC-001 §3.1 Safety"),
    ("Lead titles and clips with the person and their truth, not the tragic event.",                  "TIC-001 §3.5 Empowerment"),
    ("No clichés or inspiration-porn. Keep language real and human.",                                 "TIC-001 §3.5 (Stella Young)"),
    ("Do not invent quotes, facts or details not in the transcript.",                                 "TIC-001 §5.3 rule 3"),
    ("No medical or clinical claims, and never imply guaranteed recovery or false hope.",             "TIC-001 §3.1 / §3.3"),
    ("Shorts: cold open on the guest's most charged line, host off-screen, captions always on.",      "TIC-001 §3.1 (Shorts hook test)"),
    ("AU English. Never use em dashes.",                                                              "KH brand standard"),
]

# The dignity test every public-facing hook/title must pass (TIC-001 §3.1):
HERO_FEED_TEST = "Would the hero feel safe seeing this in their own feed?"

# ----------------------------------------------------------------------
# Language rules — used by every prompt and validated post-generation.
# ----------------------------------------------------------------------
# Banned hype / clickbait words (KH honest-curiosity style, not clickbait).
BANNED_HYPE_WORDS = [
    "journey", "inspiring", "amazing", "powerful", "resilient",
]

LANGUAGE_RULES = [
    "Australian English spelling throughout (realise, honour, recognise).",
    "Never use em dashes (—). Use a comma, full stop, or rewrite.",
    f"Never use these hype words: {', '.join(BANNED_HYPE_WORDS)}.",
    "Honest outcome only. No clickbait. Only honest curiosity gaps.",
    "Lead with the person, not the diagnosis or the tragedy.",
]

# ----------------------------------------------------------------------
# Sensitive disclosures — flag for producer review/consent before publish.
# (Was previously inline in detect.py; now the single source of truth.)
# TIC-001 §3.1 Safety + §5.3 rule 2 (mandatory review for sensitive themes).
# ----------------------------------------------------------------------
CONSENT_TRIGGERS = {
    "suicide", "suicidal", "abuse", "abused", "rape", "raped", "assault", "assaulted",
    "overdose", "self-harm", "molested", "died", "death", "relapse", "cancer",
}

# Crisis support line included in pinned comments on sensitive clips (TIC-001 §3.1).
CRISIS_SUPPORT_AU = "If this brings things up for you, Lifeline is there 24/7 on 13 11 14."

# ----------------------------------------------------------------------
# The 3 KH-fit hook formulas — patterns to RECOGNISE and lightly shape toward,
# never to fabricate. The guest's own words always win.
# ----------------------------------------------------------------------
HOOK_FORMULAS = {
    "before_after": (
        "Before / After contrast: two opposite realities collide fast. "
        "(\"I had three days to live. That was nine years ago.\")"
    ),
    "pattern_interrupt": (
        "Pattern interrupt: a jarring, specific, vulnerable line. "
        "(\"The day I got out of hospital, I didn't want to be alive.\")"
    ),
    "gold_in_brokenness": (
        "Gold in the brokenness: the paradox at the heart of KH. "
        "(\"Losing everything was the worst thing that happened to me. "
        "It was also the start of my real life.\")"
    ),
}
HOOK_FORMULA_KEYS = list(HOOK_FORMULAS.keys())  # for validating a returned label


def _bullets(items):
    return "\n".join(f"- {x}" for x in items)


# ----------------------------------------------------------------------
# Composable prompt block — rerank.py and metadata.py build their system
# prompts from THIS text, so the prompt layer has one source of truth too.
# ----------------------------------------------------------------------
SYSTEM_PROMPT_GUARDRAILS = f"""TRAUMA-INFORMED GUARDRAILS (KH-TIC-001) — these OVERRIDE everything else. \
A viral, high-reach moment that breaks any of these is not worth it. Values first, growth second.

The 7 locked rules:
{_bullets(g[0] for g in GUARDRAILS)}

Language rules:
{_bullets(LANGUAGE_RULES)}

The dignity test every hook and title must pass: {HERO_FEED_TEST}

Hook formulas to recognise and lightly shape toward (never invent words the guest did not say):
{_bullets(HOOK_FORMULAS.values())}"""


def check_language(text):
    """Layer-1 validation of generated copy. Returns a list of violation strings
    (empty list = clean). Cheap, deterministic, no network — safe to run on every
    output before it reaches a human."""
    if not text:
        return []
    if isinstance(text, (list, tuple)):      # e.g. a hashtags list
        text = " ".join(str(t) for t in text)
    issues = []
    low = text.lower()
    if "—" in text or "–" in text:
        issues.append("contains an em/en dash")
    for w in BANNED_HYPE_WORDS:
        if w in low.split() or w in low:
            issues.append(f"banned hype word: '{w}'")
    return issues
