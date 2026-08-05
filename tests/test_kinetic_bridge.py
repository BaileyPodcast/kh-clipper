"""
Tests for the Shorts Motion Graphics Upgrade Wave 2 (KH-MGX-001): the Python
bridge to the Remotion render layer (src/kinetic.py), the brand.json export
(src/export_brand.py), and clipper.py's classic/kinetic branch + fallback.
Pure logic only — no Node/npm/Chromium, so this runs anywhere (the actual
render is proven separately, see render/README.md and the PR's proof set).

    python -m pytest tests/test_kinetic_bridge.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import clipper
from src import export_brand, kinetic


# ---------------------------------------------------------------------------
# export_brand — the ONE thing that reads brand.py and writes brand.json
# ---------------------------------------------------------------------------

def test_export_brand_shape_has_no_ass_values():
    data = export_brand.build()
    # `audiogramV2` was added by the KH Audiogram v2 follow-up
    # (tests/test_audiogram_v2_bridge.py locks its own shape down) — the
    # KHKinetic-relevant top-level keys below are otherwise unchanged. "cta"
    # (Wave 2 — KH End Screen, reuses brand.CTA copy/targets verbatim) IS
    # required here, since KHKinetic.tsx's own EndScreenCta reads it.
    assert {"colours", "fonts", "caption", "animation", "cta"} <= set(data.keys())
    # hex colours (web), never ASS &HAABBGGRR values
    for v in data["colours"].values():
        assert v.startswith("#") and len(v) == 7
    assert data["animation"]["presets"]["calm"]["pop"] is False
    assert data["animation"]["presets"]["standard"]["pop"] is True
    # cta: plain px/text, never an ASS &HAABBGGRR colour
    assert data["cta"]["copy"]["subscribe"] == "Don't forget to subscribe"
    assert data["cta"]["shortsTargets"]["subscribeBtn"] == [430, 1715]
    for v in [str(x) for x in data["cta"]["copy"].values()]:
        assert not v.startswith("&H")


def test_export_brand_writes_a_real_file(tmp_path):
    out = str(tmp_path / "brand.json")
    path = export_brand.export_brand(out)
    assert path == out
    assert os.path.exists(out)
    import json
    data = json.load(open(out))
    assert data["colours"]["gold"] == "#ED9A1F"


def test_export_brand_camel_case_matches_render_types():
    # render/src/brand-types.ts expects these exact keys — lock them down so
    # Python and TypeScript can't silently drift.
    data = export_brand.build()
    assert "popFromScale" in data["animation"]
    assert "captionBands" in data["animation"]
    assert "lowFaceThreshold" in data["animation"]["captionBands"]
    assert "headingFile" in data["fonts"]
    assert "windowSec" in data["animation"]["endScreen"]
    assert "staggerMs" in data["animation"]["endScreen"]
    assert "fullEpisode" in data["cta"]["copy"]
    assert "channelProfile" in data["cta"]["shortsTargets"]


def test_export_brand_quote_card_intro_shape():
    # KH Quote Card intro (Wave 2 template 2) — render/src/KHKinetic.tsx's
    # getIntroFrames() reads brand.animation.quoteCardIntro.{enabled,durationSec}.
    data = export_brand.build()
    qci = data["animation"]["quoteCardIntro"]
    assert qci["enabled"] is True
    assert qci["durationSec"] == 1.5
    assert qci["driftPx"] == 24


# ---------------------------------------------------------------------------
# kinetic.py — availability guard + ffprobe helpers
# ---------------------------------------------------------------------------

def test_available_false_without_node_modules(monkeypatch, tmp_path):
    fake_cli = tmp_path / "render-cli.mjs"
    fake_cli.write_text("// stub")
    monkeypatch.setattr(kinetic, "RENDER_CLI", str(fake_cli))
    monkeypatch.setattr(kinetic, "RENDER_DIR", str(tmp_path))
    assert kinetic.available() is False   # no node_modules dir
    (tmp_path / "node_modules").mkdir()
    assert kinetic.available() is True


def test_finish_raises_cleanly_when_unavailable(monkeypatch):
    monkeypatch.setattr(kinetic, "available", lambda: False)
    try:
        kinetic.finish("clip.mp4", [], "out")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "render/node_modules" in str(e) or "unavailable" in str(e)


def test_finish_threads_quote_card_intro_flag_to_the_node_cli(monkeypatch, tmp_path):
    # KH Quote Card intro (Wave 2 template 2): finish()'s quote_card_intro=True
    # must append --quote-card-intro to the render-cli.mjs command; off by
    # default (the flag must not appear at all when False).
    monkeypatch.setattr(kinetic, "available", lambda: True)
    monkeypatch.setattr(export_brand, "export_brand", lambda *a, **k: str(tmp_path / "brand.json"))
    monkeypatch.setattr(kinetic, "_duration", lambda p: 6.0)
    monkeypatch.setattr(kinetic, "_fps", lambda p: 30.0)

    captured = {}

    class FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        # kinetic.finish() reads the rendered file back — stand one up.
        out_path = cmd[cmd.index("--out") + 1]
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(b"fake mp4 bytes")
        return FakeResult()

    monkeypatch.setattr(kinetic.subprocess, "run", fake_run)

    out_base = str(tmp_path / "clip")
    kinetic.finish("clip.mp4", [], out_base, banner="A hard chapter",
                    quote_card_intro=True, variants=("shorts",))
    assert "--quote-card-intro" in captured["cmd"]

    captured.clear()
    kinetic.finish("clip.mp4", [], out_base, banner="A hard chapter",
                    quote_card_intro=False, variants=("shorts",))
    assert "--quote-card-intro" not in captured["cmd"]


# ---------------------------------------------------------------------------
# clipper._finish — the classic/kinetic branch, and the fallback-on-failure
# ---------------------------------------------------------------------------

def test_finish_dispatches_to_classic_by_default(monkeypatch):
    calls = []
    monkeypatch.setattr(clipper.caption, "finish",
                        lambda *a, **k: calls.append(("classic", k)) or ["a.mp4"])
    monkeypatch.setattr(clipper.kinetic, "finish",
                        lambda *a, **k: calls.append(("kinetic", k)) or ["b.mp4"])
    out = clipper._finish("v.mp4", [], "out", banner=None, highlight_word="x",
                          safety="ok", clip_index=0, caption_style="classic")
    assert out == ["a.mp4"]
    assert [c[0] for c in calls] == ["classic"]


def test_finish_dispatches_to_kinetic_when_requested():
    calls = []
    orig = clipper.kinetic.finish
    clipper.kinetic.finish = lambda *a, **k: calls.append("kinetic") or ["k.mp4"]
    try:
        out = clipper._finish("v.mp4", [], "out", banner=None, highlight_word=None,
                              safety="ok", clip_index=0, caption_style="kinetic")
        assert out == ["k.mp4"]
        assert calls == ["kinetic"]
    finally:
        clipper.kinetic.finish = orig


def test_finish_falls_back_to_classic_when_kinetic_fails(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no node_modules")
    calls = []
    monkeypatch.setattr(clipper.kinetic, "finish", boom)
    monkeypatch.setattr(clipper.caption, "finish",
                        lambda *a, **k: calls.append("classic") or ["fallback.mp4"])
    out = clipper._finish("v.mp4", [], "out", banner=None, highlight_word=None,
                          safety="ok", clip_index=0, caption_style="kinetic")
    assert out == ["fallback.mp4"]     # the clip is never lost
    assert calls == ["classic"]


def test_finish_passes_clip_index_through_to_classic_only(monkeypatch):
    # clip_index (punch-in direction) is a classic/libass-only concept (Wave 1);
    # kinetic.finish() has no such param — must not be passed to it.
    seen = {}

    def fake_kinetic_finish(clip_in, words, out_base, banner=None, highlight_word=None,
                            safety="ok"):
        seen["kinetic_kwargs_ok"] = True
        return ["k.mp4"]
    monkeypatch.setattr(clipper.kinetic, "finish", fake_kinetic_finish)
    clipper._finish("v.mp4", [], "out", banner=None, highlight_word=None,
                    safety="ok", clip_index=3, caption_style="kinetic")
    assert seen.get("kinetic_kwargs_ok")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
        except TypeError:
            continue          # needs pytest fixtures (monkeypatch/tmp_path) — skip standalone
        print(f"ok  {fn.__name__}")
        passed += 1
    print(f"\n{passed} passed")
