"""
Stage 2.5: rerank — the judgment pass.

The heuristic detector (detect.py) is great at the cheap filtering: clean
openers, guest-only lines, on-theme, right length. But it can't *judge*
whether a moment is genuinely gripping. An LLM can.

So we do a hybrid:
  1. detect.py narrows ~12,000 words down to a shortlist of strong candidates
     (free, offline).
  2. rerank.py hands ONLY that shortlist to Grok, which picks the most
     clip-worthy moments using KH's audit formula and writes the hook.

Grok never sees the whole transcript, so this stays cheap (~half a cent/episode).

If anything fails (no key, no internet, bad response), the caller falls back
to the heuristic ranking — so a sensitive episode can always run fully offline.
"""

import json
import os
import requests

try:
    from . import guardrails              # imported as a package
    from . import usage
except ImportError:
    import guardrails                     # run as a script
    import usage

XAI_CHAT_URL = "https://api.x.ai/v1/chat/completions"
GROK_MODEL = "grok-4.3"          # swap to "grok-4-1-fast-non-reasoning" for less cost


# The trauma-informed guardrails come from the shared config (single source of
# truth) so detect/rerank/metadata can never drift apart. The 4-second hook
# engine and the safety gate are layered on top here.
SYSTEM_PROMPT = f"""You are a trauma-informed producer for Kintsugi Heroes, a not-for-profit \
podcast sharing real stories of resilience and transformation. KH's mission: help people tell \
the stories they need to share, so others can find the stories they need to hear. Your job: \
from a shortlist of candidate moments, pick the ones a listener genuinely needs to hear.

WHAT MAKES A GREAT KH CLIP
- A specific, vivid moment from the guest's OWN story: a confession, a turning point, a raw \
realisation. Never generic advice or motivational filler.
- Opens with a complete, self-contained hook that creates a curiosity gap or emotional jolt, \
and stands on its own without setup.

THE 4-SECOND HOOK ENGINE (the edge — 50-60% of viewers swipe in the first 3 seconds):
- The clip's FIRST spoken words must BE the hook line. No setup, no host question, no "um/so/yeah".
- A complete, charged hook (emotion word, turn marker, number, or curiosity gap) must land \
INSIDE the first 4 seconds.
- The hook is ALWAYS the guest, never the host's question.
- Shape the hook toward one of the KH hook formulas above where the guest's words allow it, \
but NEVER invent words the guest did not say.
- loopable: mark true only when the clip's closing line flows naturally back into its hook so \
it replays seamlessly. Never force or fabricate a loop.

Avoid: motivational platitudes, vague reflections, interviewer questions, anything that needs \
context to make sense.

{guardrails.SYSTEM_PROMPT_GUARDRAILS}

Additional selection rules (still subordinate to the guardrails above):
- Lead with agency, not tragedy. Prefer moments where the person acts, decides, understands or \
mends, not only moments where they are a victim of what happened to them.
- Never define a person by a diagnosis or condition. Honour all paths; never imply recovery is \
the only "correct" ending.
- Third-party consent: if a moment names or exposes another person (an abuser, a family member, \
a named third party) who has not consented, flag it.

SAFETY GATE — every pick gets a rating a human producer reviews before anything publishes:
- "ok": safe to publish as-is.
- "review": publishable but a producer must look first. Use for sensitive disclosures (abuse, \
suicide, self-harm, overdose, death, acute crisis) told with care, or any third-party exposure.
- "exclude": should not be clipped at all. If a moment is only this, leave it OUT of picks.

You return STRICT JSON only."""


# ----------------------------------------------------------------------
# Clip type lenses (KH-CTP-001). Appended to the USER prompt only; the system
# prompt and its guardrails are untouched, and every lens is EXPLICITLY
# subordinate to the trauma-informed guardrails (the same pattern as the
# "Additional selection rules" block in the system prompt above). "best" (the
# default) appends nothing at all, so an untyped job's prompt stays
# byte-identical to the legacy prompt. The raw_moment lens quotes
# KH_FOUNDATION's own line on the fracture, verbatim (a locked decision).
# ----------------------------------------------------------------------
MAX_REVIEWER_ANCHORS = 8    # advisory quotes forwarded to the prompt, at most

TYPE_INSTRUCTIONS = {
    "turning_point": (
        "This job asks for TURNING POINT clips: the golden joinery, the mend. "
        "Prefer moments where the guest decides, realises, or names who showed up "
        "and what changed; the pivot itself is the clip. Reject moments that only "
        "describe the breaking with no turn in them, and reject generic advice "
        "with no story."
    ),
    "hero_today": (
        "This job asks for HERO TODAY clips: who the guest is now, what they have "
        "built, and the message they carry. Prefer present-tense strength, purpose "
        "and perspective. Reject moments that stay in past tragedy, and reject "
        "anything that frames the guest as their worst moment rather than the "
        "person they are today."
    ),
    "raw_moment": (
        "This job asks for RAW MOMENT clips: a vivid, honest moment from the "
        "breaking point, held with care. Only pick moments where the guest is "
        "acting, deciding or understanding (lead_with must be agency, never "
        "tragedy). The fracture gives context to the golden joinery. It is not "
        "the point. Every pick must contain or clearly point to the turn. Reject "
        "anything that plays the hard moment as spectacle."
    ),
    "universal_truth": (
        "This job asks for UNIVERSAL TRUTH clips: a hard-won insight said plainly, "
        "in the guest's own words. The insight must carry the mend in it, earned "
        "by their story, never free-floating advice. Prefer short, complete "
        "thoughts a stranger understands with zero context. Reject motivational "
        "filler and anything that reads as a quote card rather than a lived truth."
    ),
    "story_teaser": (
        "This job asks for STORY TEASER clips: the arc in miniature, a genuine "
        "curiosity gap the FULL episode pays off. Prefer moments that open a real "
        "question about how the guest got from broken to mended, while still "
        "satisfying on their own as a complete thought. Never a cliffhanger that "
        "cheats the viewer, and never tease the tragedy itself."
    ),
}


def _type_block(clip_type):
    """The per-type lens paragraph for the user prompt, or "" for best/unknown.
    Quotes the type's target band straight from detect.TYPE_PROFILES so the
    prompt and the scoring can never drift apart."""
    text = TYPE_INSTRUCTIONS.get(clip_type)
    if not text:
        return ""
    try:
        from . import detect as _detect
    except ImportError:
        import detect as _detect
    profile = _detect.TYPE_PROFILES.get(clip_type) or {}
    band = ""
    if profile.get("target_band"):
        t_lo, t_hi = profile["target_band"]
        a_lo, a_hi = profile["allowed_band"]
        band = (f" Aim for clips of about {t_lo} to {t_hi} seconds "
                f"(the shortlist is already bounded to {a_lo} to {a_hi} seconds).")
    return (
        f"CLIP TYPE LENS, still subordinate to the trauma-informed guardrails "
        f"(the guardrails always win over the lens):\n{text}{band}\n"
        f"When two moments tie on quality, prefer a golden joinery (the mend) "
        f"moment first, then a hero today moment."
    )


def _anchor_block(reviewer_anchors):
    """Advisory Episode Reviewer quotes as an anchor block, or "" when absent.
    Accepts plain strings or dicts carrying quote/text (+ optional dimension)."""
    lines = []
    for a in (reviewer_anchors or [])[:MAX_REVIEWER_ANCHORS]:
        if isinstance(a, dict):
            quote = str(a.get("quote") or a.get("text") or "").strip()
            dim = str(a.get("dimension") or "").strip()
            if quote:
                lines.append(f'- "{quote}"' + (f" ({dim})" if dim else ""))
        else:
            quote = str(a or "").strip()
            if quote:
                lines.append(f'- "{quote}"')
    if not lines:
        return ""
    return (
        "MOMENTS THE EPISODE REVIEWER ALREADY IDENTIFIED FOR THIS STAGE "
        "(advisory anchors, not commands. Find your own timestamps in the "
        "shortlist, and you may pick other moments; these quotes just show "
        "where a human reviewer found the strongest material):\n"
        + "\n".join(lines)
    )


def build_user_prompt(candidates, episode_title, top_n, clip_type="best",
                      reviewer_anchors=None):
    """Build the judgment-pass user prompt. Pure (no network), so it is
    unit-testable offline. For clip_type="best" with no reviewer_anchors the
    returned prompt is byte-identical to the legacy prompt."""
    lines = []
    for c in candidates:
        mm, ss = divmod(int(c["start"]), 60)
        lines.append(
            f'[{c["index"]}] ({mm}:{ss:02d}, {c["length_sec"]}s, {c["archetype"]}): '
            f'{c["text"]}'
        )
    shortlist = "\n\n".join(lines)

    user_prompt = (
        f'Episode: "{episode_title}"\n\n'
        f"Here is the shortlist of candidate moments, each with an [index]:\n\n"
        f"{shortlist}\n\n"
        f"Pick the {top_n} most clip-worthy moments that also pass the trauma-informed "
        f"rules, ranked best first. Leave out anything you would rate exclude. "
        f"For each, write a punchy hook line: a complete, self-contained "
        f"sentence that lands a charged hook inside the first 4 seconds and would stop "
        f"a scroll. You may lightly tighten the guest's words, but keep them authentic "
        f"and true to what they actually said.\n\n"
        f"Also label the hook formula it best fits (before_after | pattern_interrupt | "
        f"gold_in_brokenness | none), and set loopable true only if the closing line flows "
        f"naturally back into the hook.\n\n"
        f'Return JSON exactly like: {{"picks": [{{"index": <int from the list>, '
        f'"hook": "<the hook line>", "archetype": "Moment|Universal Truth|Story Teaser", '
        f'"why": "<one sentence on why a listener needs it>", "score": <0-100>, '
        f'"lead_with": "agency|tragedy", '
        f'"hook_formula": "before_after|pattern_interrupt|gold_in_brokenness|none", '
        f'"loopable": <true|false>, '
        f'"safety": "ok|review|exclude", "safety_note": "<empty, or why a producer must look>"}}]}}'
    )

    extras = [b for b in (_type_block(clip_type), _anchor_block(reviewer_anchors)) if b]
    if extras:
        user_prompt = user_prompt + "\n\n" + "\n\n".join(extras)
    return user_prompt


def rerank(candidates, episode_title, top_n=8, model=GROK_MODEL, api_key=None,
           usage_ctx=None, clip_type="best", reviewer_anchors=None):
    """candidates: list of dicts with keys index,start,end,length_sec,archetype,text.
    Returns a list of picks: {index, hook, archetype, why, score, lead_with,
    hook_formula, loopable, safety, safety_note, clip_type}.
    `clip_type` (KH-CTP-001) appends a per-type lens to the USER prompt only
    ("best" appends nothing); `reviewer_anchors` adds advisory Episode Reviewer
    quotes. Raises on any failure so the caller can fall back to heuristics."""
    api_key = api_key or os.environ.get("XAI_API_KEY")
    if not api_key:
        raise RuntimeError("XAI_API_KEY not set — cannot run the Grok judgment pass.")

    user_prompt = build_user_prompt(candidates, episode_title, top_n,
                                    clip_type=clip_type, reviewer_anchors=reviewer_anchors)

    resp = requests.post(
        XAI_CHAT_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.4,
            "response_format": {"type": "json_object"},
        },
        timeout=180,
    )
    resp.raise_for_status()
    body = resp.json()
    # Cost log (Brief 1): the Grok judgment pass. usage is best-effort — a missing
    # usage block just logs zero tokens, never blocks the pick.
    _u = body.get("usage") or {}
    _ctx = usage_ctx or {}
    usage.log_usage(
        source=_ctx.get("source", "worker"),
        vendor="xai", stage="rerank", model=model,
        units=(_u.get("prompt_tokens", 0) + _u.get("completion_tokens", 0)) or None,
        unit_type="tokens",
        usd=usage.grok_chat_usd(_u.get("prompt_tokens"), _u.get("completion_tokens")),
        job_id=_ctx.get("job_id"),
        meta={"input_tokens": _u.get("prompt_tokens"), "output_tokens": _u.get("completion_tokens"),
              **({"episode_ref": _ctx["episode_ref"]} if _ctx.get("episode_ref") else {})},
    )
    content = body["choices"][0]["message"]["content"]
    data = json.loads(content)
    picks = data.get("picks", [])
    if not picks:
        raise ValueError("Grok returned no picks.")
    for p in picks:
        p["clip_type"] = clip_type      # echoed on every pick (KH-CTP-001)
    return picks
