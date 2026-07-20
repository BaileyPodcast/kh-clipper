"""
Stage 1: transcribe — audio -> text + word-level timestamps.

Two providers, swappable:
  - "grok"     (default) : xAI Grok STT API. ~$0.10/hr. Off your Mac.
  - "whisperx" (fallback): local, free, private. Heavier on the Mac.

Both return the SAME normalised shape so later stages don't care
which one ran:

    {
        "provider": "grok" | "whisperx",
        "text": "full transcript ...",
        "words": [ {"text": "Hello", "start": 0.24, "end": 0.48}, ... ],
    }
"""

import os
import requests

try:
    from . import usage                    # imported as a package
except ImportError:
    import usage                           # run as a script

XAI_STT_URL = "https://api.x.ai/v1/stt"


# ---------- public entry point ----------

def transcribe(audio_path, provider="grok", language="en", keyterm=None, usage_ctx=None):
    if provider == "grok":
        return _grok(audio_path, language=language, keyterm=keyterm, usage_ctx=usage_ctx)
    elif provider == "whisperx":
        return _whisperx(audio_path, language=language, usage_ctx=usage_ctx)
    raise ValueError(f"Unknown provider: {provider!r} (use 'grok' or 'whisperx')")


def _log_stt(words, provider, usage_ctx):
    """Record a STT call in ai_usage_costs (Brief 1). WhisperX is $0 but still
    logs its audio-seconds so local volume is visible. Best-effort, never raises."""
    ctx = usage_ctx or {}
    seconds = usage.audio_seconds_from_words(words)
    if provider == "grok":
        model, usd = "grok-stt", usage.grok_stt_usd(seconds)
    else:
        model, usd = "whisperx", 0.0
    usage.log_usage(
        source=ctx.get("source", "worker"),
        vendor="xai" if provider == "grok" else "whisperx",
        stage="stt", model=model, units=seconds, unit_type="audio_seconds", usd=usd,
        job_id=ctx.get("job_id"),
        meta={"transcript_source": ("grok_stt" if provider == "grok" else "whisperx"),
              **({"episode_ref": ctx["episode_ref"]} if ctx.get("episode_ref") else {})},
    )


# ---------- provider: Grok STT (API) ----------

def _grok(audio_path, language="en", keyterm=None, usage_ctx=None):
    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        raise RuntimeError("XAI_API_KEY not set in environment.")

    # Non-file fields go in `data`; the file goes in `files`. requests sends
    # data fields first, so the file ends up LAST in the form — which the
    # xAI STT endpoint requires.
    # diarize=true makes the API tag each word with an integer `speaker`,
    # so Stage 2 can tell the guest's story apart from the host's questions.
    data = {"format": "true", "language": language, "diarize": "true"}
    if keyterm:
        data["keyterm"] = keyterm

    with open(audio_path, "rb") as f:
        resp = requests.post(
            XAI_STT_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            data=data,
            files={"file": (os.path.basename(audio_path), f)},
            timeout=3600,
        )
    if not resp.ok:                       # surface xAI's reason, not a bare 400
        sz = os.path.getsize(audio_path)
        raise RuntimeError(f"Grok STT {resp.status_code} ({sz} bytes audio): {resp.text[:400]}")
    body = resp.json()

    words = [
        {"text": w["text"], "start": w["start"], "end": w["end"],
         "speaker": w.get("speaker")}
        for w in body.get("words", [])
    ]
    _log_stt(words, "grok", usage_ctx)
    return {
        "provider": "grok",
        "text": body.get("text", ""),
        "words": words,
    }


# ---------- provider: WhisperX (local fallback) ----------

def _whisperx(audio_path, language="en", usage_ctx=None):
    import whisperx

    # Mac mini = CPU + int8 (fastest/lowest-memory combo that works on Apple).
    device = "cpu"
    compute_type = "int8"

    model = whisperx.load_model("large-v3", device, compute_type=compute_type)
    audio = whisperx.load_audio(audio_path)
    result = model.transcribe(audio, batch_size=8, language=language)

    # Second pass = word-level alignment (the bit a clipper actually needs).
    align_model, meta = whisperx.load_align_model(
        language_code=result["language"], device=device
    )
    aligned = whisperx.align(result["segments"], align_model, meta, audio, device)

    words, parts = [], []
    for seg in aligned["segments"]:
        for w in seg.get("words", []):
            if "start" in w and "end" in w:  # alignment can drop a stray token
                words.append({"text": w["word"], "start": w["start"], "end": w["end"]})
                parts.append(w["word"])

    _log_stt(words, "whisperx", usage_ctx)
    return {
        "provider": "whisperx",
        "text": " ".join(parts).strip(),
        "words": words,
    }
