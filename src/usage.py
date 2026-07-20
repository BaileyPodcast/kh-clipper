"""
Per-job AI cost log (Spring Clean Brief 1) — worker side.

Every external AI call the worker makes (Grok STT, Grok rerank, Grok copy, and
after Brief 3 the Claude copy call) records what it cost into the shared
`ai_usage_costs` table in the Production Pipeline Supabase, so spend by vendor /
job / month is one query across BOTH the app and the worker.

This module is self-contained on purpose: the pipeline stages in src/ are pure
and never import worker/app.py, so log_usage reads SUPABASE_URL /
SUPABASE_SERVICE_ROLE_KEY straight from the env the Modal worker already sets
(the same service-role key that patches shorts_jobs; it bypasses RLS). It NEVER
raises — a logging failure must never break a render.

The worker's "episode" is a YouTube/Drive id (text), not the app's studio-pack
uuid, so we send episode_id=NULL and stash that reference in `meta.episode_ref`.
`job_id` is the shorts_jobs uuid, which IS a valid uuid, so it links directly.
"""

import json
import os

import requests


# ── Vendor prices (dated constants, easy to correct) ─────────────────────────
# Last reviewed 2026-07-20. Keep this the single price block for the worker.
GROK_STT_USD_PER_HOUR = 0.10          # xAI Grok STT, per audio-hour
# xAI Grok chat (grok-4.3), USD per 1M tokens (input / output). ESTIMATE — confirm
# against current xAI published rates.
GROK_CHAT_USD_PER_MTOK = {"in": 3.0, "out": 15.0}
# Anthropic Claude (Brief 3 copy swap), USD per 1M tokens. Mirror the app's
# lib/usage.ts anthropic_per_mtok. ESTIMATE for the pinned model — confirm.
ANTHROPIC_USD_PER_MTOK = {
    "claude-haiku-4-5": {"in": 1.0, "out": 5.0},
    "claude-sonnet-4-6": {"in": 3.0, "out": 15.0},
}


def grok_stt_usd(audio_seconds):
    """Dollar cost of a Grok STT call for `audio_seconds` of audio."""
    try:
        return (float(audio_seconds) / 3600.0) * GROK_STT_USD_PER_HOUR
    except (TypeError, ValueError):
        return 0.0


def grok_chat_usd(input_tokens, output_tokens):
    """Dollar cost of a Grok chat call from its usage token counts."""
    try:
        return (float(input_tokens or 0) * GROK_CHAT_USD_PER_MTOK["in"]
                + float(output_tokens or 0) * GROK_CHAT_USD_PER_MTOK["out"]) / 1_000_000.0
    except (TypeError, ValueError):
        return 0.0


def anthropic_usd(model, input_tokens, output_tokens):
    """Dollar cost of an Anthropic call from model + usage token counts."""
    p = ANTHROPIC_USD_PER_MTOK.get(model, {"in": 1.0, "out": 5.0})
    try:
        return (float(input_tokens or 0) * p["in"]
                + float(output_tokens or 0) * p["out"]) / 1_000_000.0
    except (TypeError, ValueError):
        return 0.0


def _headers(key):
    h = {"apikey": key, "Authorization": f"Bearer {key}",
         "Content-Type": "application/json", "Prefer": "return=minimal"}
    # HTTP headers must be latin-1 encodable; drop stray non-ascii so a bad paste
    # in a secret can never crash the request (mirrors worker/app.py).
    return {k: str(v).encode("ascii", "ignore").decode() for k, v in h.items()}


def log_usage(*, vendor, stage, usd, source="worker", model=None, units=None,
              unit_type=None, episode_id=None, job_id=None, meta=None):
    """Insert one ai_usage_costs row via the service-role REST endpoint. Never
    raises: any problem (missing env, network, non-2xx) is printed and swallowed
    so a logging failure can never affect a render."""
    try:
        url = (os.environ.get("SUPABASE_URL") or "").strip()
        key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        if not url or not key:
            print("[usage] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set — skipping cost log")
            return
        row = {
            "source": source,
            "vendor": vendor,
            "stage": stage,
            "model": model,
            "units": units,
            "unit_type": unit_type,
            "usd": float(usd or 0),
            "episode_id": episode_id,   # uuid or None (worker refs go in meta)
            "job_id": job_id,           # shorts_jobs uuid or None
            "meta": meta or {},
        }
        body = json.dumps(row, ensure_ascii=True)
        r = requests.post(f"{url}/rest/v1/ai_usage_costs",
                          headers=_headers(key), data=body, timeout=30)
        if r.status_code >= 300:
            print(f"[usage] insert {r.status_code}: {r.text[:200]}")
    except Exception as e:                # cost logging is best-effort
        print(f"[usage] log_usage failed: {e}")


def audio_seconds_from_words(words):
    """Best available audio duration for a transcript: the last word's end time."""
    try:
        return max((float(w.get("end", 0) or 0) for w in (words or [])), default=0.0)
    except (TypeError, ValueError):
        return 0.0
