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

XAI_STT_URL = "https://api.x.ai/v1/stt"


# ---------- public entry point ----------

def transcribe(audio_path, provider="grok", language="en", keyterm=None):
    if provider == "grok":
        return _grok(audio_path, language=language, keyterm=keyterm)
    elif provider == "whisperx":
        return _whisperx(audio_path, language=language)
    raise ValueError(f"Unknown provider: {provider!r} (use 'grok' or 'whisperx')")


# ---------- provider: Grok STT (API) ----------

def _grok(audio_path, language="en", keyterm=None):
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
    return {
        "provider": "grok",
        "text": body.get("text", ""),
        "words": words,
    }


# ---------- provider: WhisperX (local fallback) ----------

def _whisperx(audio_path, language="en"):
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

    return {
        "provider": "whisperx",
        "text": " ".join(parts).strip(),
        "words": words,
    }
