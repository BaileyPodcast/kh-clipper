"""
Stage 2.7: metadata, the per-Short metadata pack (ready to post).

Detect/rerank give us the clip, the hook, the why, the archetype and the transcript
text. This stage turns each into a copy-paste metadata pack so there's no manual step
before publishing. The COPY follows the locked KH Shorts v5 standard, assembled by
packaging.py (the worker's mirror of the app's shorts-packaging.ts):

  title          the short render-facing hook (the audiogram footer + on-screen
                 banner read it; the app composes the 100-char YouTube upload title)
  description    the locked 7-part v5 block (hook+SEO line, context, links, About-KH,
                 series blurb, subscribe, 15 hashtags)
  hashtags       exactly the locked 15, in order
  tags           the 3-group tags field (Post Specific, Niche, Broad), flat
  pinned_comment full-episode link + one open engagement question (crisis line on
                 sensitive clips)
  banner_hook    the short on-screen banner headline (curiosity-gap framing)

KH-specific, trauma-informed, AU English, no em dashes. NO VidIQ, all scoring/voice
is KH's own. Grok fills only the creative slots; packaging.py assembles the locked
scaffolding. The values rules come from guardrails.py (single source of truth).

Like rerank, this calls Grok and RAISES on any failure so the caller can carry on
without metadata (the videos still cut; the producer just fills metadata manually).
"""

import json
import os
import re

try:
    from . import guardrails              # imported as a package
    from . import brand
    from . import packaging
    from . import usage
except ImportError:
    import guardrails                     # run as a script
    import brand
    import packaging
    import usage

# Shorts copy is written by Claude (Spring Clean Brief 3), the one Grok job where
# model quality matters most: the KH voice rules run through the model that follows
# them best. Match the model kh-studio pins for shorts copy (lib/studio/ai.ts
# MODELS.fast) so app-written and worker-written copy come from the same model.
# Grok still handles STT and moment ranking; this is copy only.
ANTHROPIC_MODEL = "claude-haiku-4-5"

# Fields checked for banned words / em dashes (the render-facing title + banner and
# the assembled copy). tags/hashtags come from the locked packaging module.
METADATA_FIELDS = ("title", "description", "pinned_comment", "banner_hook")

# The Grok call now fills ONLY the creative slots; packaging.py assembles the locked
# v5 scaffolding (7-part description, 15 hashtags in order, 3-group tags, links,
# blurbs, cadence) around them, so a worker-packaged Short matches an app-packaged
# one for the same episode. See SHORTS-WORKER-CONTRACT.md.
SYSTEM_PROMPT = f"""You write the publishing metadata for Kintsugi Heroes Shorts: a \
not-for-profit podcast sharing real stories of resilience and transformation. You are \
trauma-informed first and a growth-minded producer second. NEVER use VidIQ-style \
clickbait scoring; KH has its own honest voice.

{guardrails.SYSTEM_PROMPT_GUARDRAILS}

The fixed scaffolding (About Kintsugi Heroes, the series blurb, the 15 hashtags in \
their locked order, the tags field, the links and subscribe line) is assembled by the \
app, not by you. FOR EACH CLIP, produce ONLY these creative slots:
- title: a short, honest-curiosity title in KH's voice (the hook only, no brand \
suffix, a few words). A real curiosity gap, never clickbait, never a banned hype word. \
Lead with the person, not the diagnosis. Would the guest feel safe seeing this?
- hook_seo_line: ONE human sentence under 150 characters carrying the emotional hook \
AND 2 to 3 searchable keywords (the clip topic, "Kintsugi Heroes", the series name or \
"Australian" where natural). Never keyword-stuffed.
- context: 2 to 3 natural sentences from the transcript. Who, what, why it matters. \
Lead with the person and the turning point, no spoilers of the full episode.
- primary_topic: the single best topic for THIS clip, from: {", ".join(packaging.TOPIC_TAGS.keys())}.
- secondary_topics: up to 3 more from that same list, different from the primary.
- post_specific_tags: 3 to 6 clip-specific plain keywords (exact subject, key phrases). No hashes.
- pinned_question: one open-ended question tied to THIS clip's theme that invites a \
safe reply. NEVER ask anyone to disclose trauma in the comments.
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


def _call_model(system_prompt, user_prompt, model, api_key):
    """One Anthropic Messages call for the creative slots. Returns (text, usage
    dict). Isolated so generate() stays focused on assembly (and so tests can swap
    it). `anthropic` is imported lazily so importing this module never requires the
    SDK (e.g. in a packaging-only test env)."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=8000,
        temperature=0.5,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = "".join(getattr(b, "text", "") for b in resp.content
                   if getattr(b, "type", None) == "text")
    u = resp.usage
    return text, {"input_tokens": getattr(u, "input_tokens", 0) or 0,
                  "output_tokens": getattr(u, "output_tokens", 0) or 0}


def _extract_packs(text):
    """Parse the {"packs":[...]} object from Claude's text response, tolerating a
    ```json fence or stray prose around it. Raises on no/incomplete JSON so the
    caller falls back to no-metadata (the videos still cut)."""
    s = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", s, re.IGNORECASE)
    if fence:
        s = fence.group(1).strip()
    start = s.find("{")
    if start == -1:
        raise ValueError("No JSON object in the model response.")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(s[start:i + 1])
    raise ValueError("Incomplete JSON in the model response.")


def generate(clips, episode_title, episode_url, handle=None,
             model=ANTHROPIC_MODEL, api_key=None, guest_name=None, series=None, usage_ctx=None):
    """Attach a `metadata` dict to each clip (in place) and return the clips.
    Raises on any failure so the caller can fall back to no-metadata.

    `guest_name` (when known) is the real guest's name; Grok uses it naturally in
    the title/context/pinned instead of "our guest". `series` is the worker series
    slug, used to build the series-correct hashtags, tags and blurb."""
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set, cannot generate the metadata pack.")
    if not clips:
        return clips
    handle = handle or brand.CTA["copy"]["handle"]

    guest_line = (
        f'The guest\'s name is "{guest_name}". Use it naturally where you would otherwise '
        f'say "our guest" or "this guest"; still lead with the person, never the '
        f'diagnosis or the tragedy.\n'
        if guest_name else
        "The guest's name is not provided, use warm, generic wording (no invented name).\n"
    )
    blocks = "\n\n".join(_clip_block(i, c) for i, c in enumerate(clips))
    user_prompt = (
        f'Episode: "{episode_title}"\n'
        f"{guest_line}"
        f"Here are the {len(clips)} clips, each with an [index]:\n\n{blocks}\n\n"
        f'Return JSON exactly like: {{"packs": [{{"index": <int>, '
        f'"title": "<short hook title>", "hook_seo_line": "<under 150 chars>", '
        f'"context": "<2-3 sentences>", "primary_topic": "<topic>", '
        f'"secondary_topics": ["<topic>", "<topic>"], '
        f'"post_specific_tags": ["<keyword>", "<keyword>"], '
        f'"pinned_question": "<one open question>", '
        f'"banner_hook": "<short banner headline>"}}]}}'
    )

    text, tok = _call_model(SYSTEM_PROMPT, user_prompt, model, api_key)
    # Cost log (Brief 1 + 3): the Shorts copy pass, now Claude. Best-effort; never
    # blocks the pack.
    _ctx = usage_ctx or {}
    usage.log_usage(
        source=_ctx.get("source", "worker"),
        vendor="anthropic", stage="copy", model=model,
        units=(tok["input_tokens"] + tok["output_tokens"]) or None,
        unit_type="tokens",
        usd=usage.anthropic_usd(model, tok["input_tokens"], tok["output_tokens"]),
        job_id=_ctx.get("job_id"),
        meta={"input_tokens": tok["input_tokens"], "output_tokens": tok["output_tokens"],
              **({"episode_ref": _ctx["episode_ref"]} if _ctx.get("episode_ref") else {})},
    )
    data = _extract_packs(text)
    packs = data.get("packs", [])
    if not packs:
        raise ValueError("Claude returned no metadata packs.")

    by_index = {p.get("index"): p for p in packs if p.get("index") is not None}
    for i, clip in enumerate(clips):
        p = by_index.get(i)
        if not p:
            continue

        # Assemble the locked v5 package from the creative slots (single source of
        # truth in packaging.py, mirroring the app's shorts-packaging.ts).
        hashtags = packaging.compose_hashtags(
            series, p.get("primary_topic"), p.get("secondary_topics"))
        description = packaging.compose_description(
            series, p.get("hook_seo_line"), p.get("context"), hashtags,
            full_episode_url=episode_url)
        tags = packaging.compose_tags(
            series, hero_name=guest_name, primary_topic=p.get("primary_topic"),
            post_specific=p.get("post_specific_tags"))["flat"]
        # Sensitive clips carry the crisis-support line in the pinned comment.
        crisis = guardrails.CRISIS_SUPPORT_AU if clip.get("safety", "ok") != "ok" else None
        pinned = packaging.compose_pinned(
            p.get("pinned_question"), full_episode_url=episode_url, prefix=crisis)

        meta = {
            # title + banner_hook stay the short, render-facing copy (the audiogram
            # footer + on-screen banner read these); the app composes the 100-char
            # YouTube upload title from `title` at approval time.
            "title": (p.get("title") or "").strip(),
            "description": description,
            "hashtags": hashtags,
            "tags": tags,
            "pinned_comment": pinned,
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
