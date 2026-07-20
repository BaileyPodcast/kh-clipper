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


def rerank(candidates, episode_title, top_n=8, model=GROK_MODEL, api_key=None, usage_ctx=None):
    """candidates: list of dicts with keys index,start,end,length_sec,archetype,text.
    Returns a list of picks: {index, hook, archetype, why, score, lead_with,
    hook_formula, loopable, safety, safety_note}.
    Raises on any failure so the caller can fall back to heuristics."""
    api_key = api_key or os.environ.get("XAI_API_KEY")
    if not api_key:
        raise RuntimeError("XAI_API_KEY not set — cannot run the Grok judgment pass.")

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
    return picks
