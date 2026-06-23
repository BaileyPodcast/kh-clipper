"""
Stage 2.7: metadata — the per-Short metadata pack (ready to post).

"5 Shorts to use" means ready to post. Detect/rerank give us the clip, the hook, the
why, the archetype and the transcript text. This stage turns each into a copy-paste
metadata pack so there's no manual step before publishing:

  title         — KH honest-curiosity style (no clickbait, no banned hype words)
  description    — 2-3 sentences, natural keywords, full-episode link + @handle
  hashtags       — exactly 2-3 hyper-relevant tags (never a wall of 30)
  pinned_comment — one warm, trauma-informed line that invites safe engagement
  banner_hook    — the short on-screen banner headline (curiosity-gap framing)

KH-specific, trauma-informed, AU English, no em dashes. NO VidIQ — all scoring/voice
is KH's own. The values rules come from guardrails.py (single source of truth); the
prompt is composed from the same guardrail text rerank.py uses.

Like rerank, this calls Grok and RAISES on any failure so the caller can carry on
without metadata (the videos still cut; the producer just fills metadata manually).
"""

import json
import os
import requests

try:
    from . import guardrails              # imported as a package
    from . import brand
except ImportError:
    import guardrails                     # run as a script
    import brand

XAI_CHAT_URL = "https://api.x.ai/v1/chat/completions"
GROK_MODEL = "grok-4.3"

METADATA_FIELDS = ("title", "description", "hashtags", "pinned_comment", "banner_hook")

SYSTEM_PROMPT = f"""You write the publishing metadata for Kintsugi Heroes Shorts: a \
not-for-profit podcast sharing real stories of resilience and transformation. You are \
trauma-informed first and a growth-minded producer second. NEVER use VidIQ-style \
clickbait scoring; KH has its own honest voice.

{guardrails.SYSTEM_PROMPT_GUARDRAILS}

FOR EACH CLIP, produce a metadata pack:
- title: a short, honest-curiosity title in KH's voice. A real curiosity gap, never \
clickbait, never a banned hype word. Lead with the person, not the diagnosis or the \
tragedy. It must pass the test: would the guest feel safe seeing this in their own feed?
- description: 2 to 3 natural sentences with real keywords (no keyword stuffing). End \
with the full-episode link and the @handle.
- hashtags: EXACTLY 2 to 3 hyper-relevant tags (e.g. #shorts plus one or two niche \
tags). Never a wall of tags.
- pinned_comment: one warm, human line that invites safe engagement. NEVER prompt \
unsafe public disclosure (do not ask people to share their trauma in the comments).
- banner_hook: a short on-screen banner headline using curiosity-gap framing \
(e.g. "He can't feel his legs"). A few words, punchy, dignified.

You return STRICT JSON only."""


def _clip_block(i, clip):
    why = clip.get("why") or ""
    safety = clip.get("safety", "ok")
    flag = f"  [SENSITIVE: {safety}]" if safety != "ok" else ""
    return (
        f'[{i}] hook: "{clip.get("hook_line", "")}"\n'
        f"    archetype: {clip.get('archetype', '')}{flag}\n"
        f"    why a listener needs it: {why}\n"
        f"    transcript: {clip.get('text', '')}"
    )


def generate(clips, episode_title, episode_url, handle=None,
             model=GROK_MODEL, api_key=None):
    """Attach a `metadata` dict to each clip (in place) and return the clips.
    Raises on any failure so the caller can fall back to no-metadata."""
    api_key = api_key or os.environ.get("XAI_API_KEY")
    if not api_key:
        raise RuntimeError("XAI_API_KEY not set — cannot generate the metadata pack.")
    if not clips:
        return clips
    handle = handle or brand.CTA["copy"]["handle"]

    blocks = "\n\n".join(_clip_block(i, c) for i, c in enumerate(clips))
    user_prompt = (
        f'Episode: "{episode_title}"\n'
        f"Full-episode link to use in every description: {episode_url}\n"
        f"Handle to use: {handle}\n\n"
        f"Here are the {len(clips)} clips, each with an [index]:\n\n{blocks}\n\n"
        f"For SENSITIVE clips (flagged above), the pinned_comment must gently include "
        f"crisis support, exactly: \"{guardrails.CRISIS_SUPPORT_AU}\"\n\n"
        f'Return JSON exactly like: {{"packs": [{{"index": <int>, '
        f'"title": "<title>", "description": "<2-3 sentences + link + handle>", '
        f'"hashtags": ["#shorts", "#tag2"], "pinned_comment": "<one warm line>", '
        f'"banner_hook": "<short banner headline>"}}]}}'
    )

    resp = requests.post(
        XAI_CHAT_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.5,
            "response_format": {"type": "json_object"},
        },
        timeout=180,
    )
    resp.raise_for_status()
    data = json.loads(resp.json()["choices"][0]["message"]["content"])
    packs = data.get("packs", [])
    if not packs:
        raise ValueError("Grok returned no metadata packs.")

    by_index = {p.get("index"): p for p in packs if p.get("index") is not None}
    for i, clip in enumerate(clips):
        p = by_index.get(i)
        if not p:
            continue
        hashtags = p.get("hashtags") or []
        if isinstance(hashtags, str):
            hashtags = [h.strip() for h in hashtags.replace(",", " ").split() if h.strip()]
        hashtags = [h if h.startswith("#") else f"#{h}" for h in hashtags][:3]
        meta = {
            "title": (p.get("title") or "").strip(),
            "description": (p.get("description") or "").strip(),
            "hashtags": hashtags,
            "pinned_comment": (p.get("pinned_comment") or "").strip(),
            "banner_hook": (p.get("banner_hook") or "").strip(),
        }
        # Layer-1 validation pass: flag any banned word / em dash that slipped through
        # so the producer sees it in REVIEW.md (values check, never silently shipped).
        warnings = []
        for field in METADATA_FIELDS:
            for issue in guardrails.check_language(meta[field]):
                warnings.append(f"{field}: {issue}")
        if warnings:
            meta["_warnings"] = warnings
        clip["metadata"] = meta
    return clips
