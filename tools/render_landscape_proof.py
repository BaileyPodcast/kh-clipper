"""
Local proof render for the 16:9 landscape audiogram (KH-VRL-001 Wave 1).

Runs src/audiogram.render_landscape end to end on this machine, with no Modal,
no Supabase and no network. Given no --audio it synthesises a 75 second test
tone (a warbling sine plus pink noise, so the waveform has something to react
to) with ffmpeg first. A synthetic word list exercises the timed caption path
unless you pass --static.

    python tools/render_landscape_proof.py --out /tmp/proof.mp4
    python tools/render_landscape_proof.py --audio ep.m4a --series grit-diaries
    python tools/render_landscape_proof.py --brand brand.json --duration 30
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import audiogram


def synth_audio(path, duration):
    """A voice-ish test signal: modulated sine + pink noise, AAC in an m4a."""
    r = subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", f"sine=frequency=220:duration={duration}",
         "-f", "lavfi", "-i", f"anoisesrc=d={duration}:color=pink:amplitude=0.12",
         "-filter_complex",
         "[0]tremolo=f=0.7:d=0.9[a];[a][1]amix=inputs=2:duration=first,volume=1.6",
         "-c:a", "aac", path],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"synthetic audio failed: {r.stderr[-500:]}")
    return path


def synth_words(duration):
    """A rolling word list covering the clip, to exercise the timed captions."""
    script = ("this is a local proof render of the landscape audiogram "
              "the caption line swaps every few seconds "
              "while the gold bars follow the audio").split()
    words, t, i = [], 0.5, 0
    step = 0.42
    while t + step < duration - 0.5:
        words.append({"text": script[i % len(script)], "start": round(t, 2),
                      "end": round(t + step, 2)})
        t += step + (0.9 if (i + 1) % len(script) == 0 else 0.03)
        i += 1
    return words


def probe(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,size:stream=width,height,codec_name,codec_type",
         "-of", "json", path], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 else {}


def main():
    ap = argparse.ArgumentParser(description="Local landscape audiogram proof render")
    ap.add_argument("--audio", default=None, help="local audio file (else a 75s synthetic tone)")
    ap.add_argument("--series", default="kintsugi-heroes", help="series slug for palette/eyebrow")
    ap.add_argument("--brand", default=None, help="path to a brand override JSON block")
    ap.add_argument("--duration", type=int, default=75, help="synthetic audio length in seconds")
    ap.add_argument("--title", default="A story of repair", help="footer title")
    ap.add_argument("--guest", default="Jane Doe", help="hero name for the footer")
    ap.add_argument("--static", action="store_true", help="skip timed captions (static line)")
    ap.add_argument("--out", default="proof.mp4", help="output mp4 path")
    a = ap.parse_args()

    audio = a.audio
    if not audio:
        audio = os.path.join(os.path.dirname(os.path.abspath(a.out)) or ".", "proof_audio.m4a")
        print(f"synthesising {a.duration}s test audio -> {audio}")
        synth_audio(audio, a.duration)

    brand = None
    if a.brand:
        brand = json.load(open(a.brand))

    dur = audiogram._duration(audio)
    words = None if a.static else synth_words(dur)

    print(f"rendering landscape audiogram ({a.series}) from {audio} ...")
    out, notes = audiogram.render_landscape(
        audio, a.out, series=a.series, brand=brand, words=words,
        title=a.title, guest_name=a.guest, ep_label="EP 14", here=ROOT)
    if notes:
        print("brand fallbacks: " + "; ".join(notes))

    info = probe(out)
    print(f"wrote {out}")
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
