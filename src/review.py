"""
Stage 6: review — the producer gate sheet (REVIEW.md).

Tony's review gate should be one glance, not digging through JSON. After cut +
reframe + caption, this writes a plain-English REVIEW.md next to the finished clips
in the per-episode folder. For each clip a producer can read it top to bottom and
copy-paste the metadata without touching JSON.

Per clip:
  - number, timecode, length, archetype, hook formula, loopable
  - safety flag (ok / review + note) and framing flag
  - the hook line and the one-sentence "why a listener needs this"
  - the full metadata pack (title, description, hashtags, pinned comment, banner) in
    copy-paste blocks

Values gate: every clip shows both a fit/hook score AND a values check (safety +
any language warnings), so growth and values are visible side by side.
"""

import os


def _fmt(t):
    try:
        t = float(t)
    except (TypeError, ValueError):
        return "?"
    m, s = divmod(int(t), 60)
    return f"{m}:{s:02d}"


def _meta_block(meta):
    if not meta:
        return ["_No metadata pack generated (fill by hand, or re-run with XAI_API_KEY set)._", ""]
    out = []
    out.append("**Title**")
    out.append("```")
    out.append(meta.get("title", ""))
    out.append("```")
    out.append("**Description**")
    out.append("```")
    out.append(meta.get("description", ""))
    out.append("```")
    out.append("**Hashtags**")
    out.append("```")
    out.append(" ".join(meta.get("hashtags", []) or []))
    out.append("```")
    out.append("**Pinned comment**")
    out.append("```")
    out.append(meta.get("pinned_comment", ""))
    out.append("```")
    out.append("**Banner hook**")
    out.append("```")
    out.append(meta.get("banner_hook", ""))
    out.append("```")
    return out


def build_markdown(result, cuts):
    """Return the REVIEW.md text for an episode."""
    cuts_by_id = {c.get("clip_id"): c for c in (cuts or [])}
    clips = result.get("clips", [])
    title = result.get("title") or result.get("source") or "Episode"

    n_ok = sum(1 for c in clips if c.get("safety", "ok") == "ok")
    n_review = len(clips) - n_ok

    L = []
    L.append(f"# Producer review — {title}")
    L.append("")
    L.append(f"**{len(clips)} clip(s)** · {n_ok} ok · {n_review} need a producer look · "
             f"judged by: {result.get('method', 'heuristic')}")
    L.append("")
    L.append("Read each clip top to bottom. Approve it, then copy-paste its metadata. "
             "Nothing here auto-publishes. **Values override reach: never ship a clip "
             "whose safety check says review until you have looked.**")
    L.append("")
    L.append("---")
    L.append("")

    for n, clip in enumerate(clips, 1):
        cid = clip.get("clip_id", f"clip-{n:02d}")
        cut = cuts_by_id.get(cid, {})
        start = cut.get("start", clip.get("start"))
        end = cut.get("end", clip.get("end"))
        length = cut.get("duration", clip.get("length_sec"))
        hf = clip.get("hook_formula", "none")
        hf_txt = f" · formula: {hf}" if hf and hf != "none" else ""
        loop_txt = " · ↻ loopable" if clip.get("loopable") else ""

        safety = clip.get("safety", "ok")
        if safety == "ok":
            safety_line = "✅ ok"
        else:
            note = clip.get("safety_note") or "sensitive — a producer must look first"
            safety_line = f"⚠️ **{safety.upper()}** — {note}"

        framing = clip.get("framing") or cut.get("framing")
        if framing and framing != "ok":
            framing_line = f"⚠️ **{framing}** — check the crop keeps the guest framed"
        else:
            framing_line = "✅ ok"

        L.append(f"## Clip {n} — `{cid}`")
        L.append("")
        L.append(f"- **Timecode:** {_fmt(start)}–{_fmt(end)}  ·  **{length}s**  ·  "
                 f"{clip.get('archetype', '')}{hf_txt}{loop_txt}")
        L.append(f"- **Score:** fit {clip.get('fit_score', '-')} · hook4s "
                 f"{clip.get('hook4s', '-')}  ·  led with: {clip.get('lead_with', '-')}")
        L.append(f"- **Safety:** {safety_line}")
        L.append(f"- **Framing:** {framing_line}")
        if cut.get("trimmed_to_cap"):
            L.append("- **Note:** trimmed to the length cap (end shortened to keep it under the ceiling)")
        L.append("")
        L.append(f"> **Hook:** \"{clip.get('hook_line', '')}\"")
        L.append("")
        if clip.get("why"):
            L.append(f"**Why a listener needs this:** {clip['why']}")
            L.append("")

        meta = clip.get("metadata")
        warnings = (meta or {}).get("_warnings")
        if warnings:
            L.append("> ⚠️ **Language check:** " + "; ".join(warnings) +
                     " — fix before publishing.")
            L.append("")
        L.extend(_meta_block(meta))
        L.append("")
        L.append("---")
        L.append("")

    return "\n".join(L)


def write_review(out_dir, result, cuts):
    """Write REVIEW.md into out_dir and return its path."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "REVIEW.md")
    with open(path, "w") as f:
        f.write(build_markdown(result, cuts))
    return path
