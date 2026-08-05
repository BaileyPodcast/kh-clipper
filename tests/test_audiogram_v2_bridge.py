"""
Tests for the Shorts Motion Graphics Upgrade Wave 2 (KH-MGX-001) follow-up:
KH Audiogram v2 — the Python bridge to the Remotion render layer
(src/audiogram_v2.py) and its extension of the brand.json export
(src/export_brand.py). Pure logic only — no Node/npm/Chromium, so this runs
anywhere (the actual render is proven separately, see render/README.md and
docs/proof/kh-mgx-001-audiogram-v2/).

    python -m pytest tests/test_audiogram_v2_bridge.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import audiogram_v2, export_brand
from src.audiogram import NBARS


# ---------------------------------------------------------------------------
# export_brand — the new `audiogramV2` key, read by render/src/KHAudiogramV2.tsx
# ---------------------------------------------------------------------------

def test_export_brand_still_has_kinetic_shape_unchanged():
    # The audiogramV2 addition must not disturb the KHKinetic export shape
    # locked by tests/test_kinetic_bridge.py.
    data = export_brand.build()
    assert set(data.keys()) == {"colours", "fonts", "caption", "animation", "audiogramV2"}


def test_export_brand_audiogram_v2_camel_case_matches_render_types():
    # render/src/audiogram-types.ts's AudiogramBrand expects these exact
    # keys — lock them down so Python and TypeScript can't silently drift.
    av2 = export_brand.build()["audiogramV2"]
    assert set(av2.keys()) == {"fonts", "bars", "progress", "captionTransition", "presets", "layout"}
    assert set(av2["fonts"].keys()) == {"headingBold", "headingXBold", "body", "mono"}
    for f in av2["fonts"].values():
        assert set(f.keys()) == {"family", "file"}
    assert "entranceStaggerMs" in av2["bars"]
    assert "springDamping" in av2["bars"]
    assert av2["bars"]["count"] == NBARS  # must match src/audiogram.py's seeded-bar count exactly
    assert set(av2["presets"]["standard"].keys()) == {"barEntranceSpring", "progressGlow"}
    assert av2["presets"]["standard"]["barEntranceSpring"] is True
    assert av2["presets"]["calm"]["barEntranceSpring"] is False
    assert av2["presets"]["calm"]["progressGlow"] is False


def test_export_brand_audiogram_v2_layout_has_all_three_formats():
    layout = export_brand.build()["audiogramV2"]["layout"]
    assert set(layout.keys()) >= {"wide", "tall", "square", "barRadius", "centreGap", "progressH", "progressGap"}
    # Landscape (wide) sizes as a fraction of the full frame width; the
    # other two use cqw (percent of the smaller dimension) — never both.
    assert "captionMaxWFrac" in layout["wide"]
    assert "captionMaxWFrac" not in layout["tall"]
    assert "captionMaxWCqw" in layout["tall"]
    assert "captionMaxWCqw" in layout["square"]


def test_export_brand_writes_a_real_file_with_audiogram_v2(tmp_path):
    out = str(tmp_path / "brand.json")
    path = export_brand.export_brand(out)
    assert path == out
    import json
    data = json.load(open(out))
    assert data["audiogramV2"]["bars"]["count"] == 44


# ---------------------------------------------------------------------------
# audiogram_v2.py — availability guard
# ---------------------------------------------------------------------------

def test_available_false_without_node_modules(monkeypatch, tmp_path):
    fake_cli = tmp_path / "render-cli.mjs"
    fake_cli.write_text("// stub")
    monkeypatch.setattr(audiogram_v2, "RENDER_CLI", str(fake_cli))
    monkeypatch.setattr(audiogram_v2, "RENDER_DIR", str(tmp_path))
    assert audiogram_v2.available() is False   # no node_modules dir
    (tmp_path / "node_modules").mkdir()
    assert audiogram_v2.available() is True


def test_finish_raises_cleanly_when_unavailable(monkeypatch):
    monkeypatch.setattr(audiogram_v2, "available", lambda: False)
    try:
        audiogram_v2.finish("clip.mp4", [], "out")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "render/node_modules" in str(e) or "unavailable" in str(e)


# ---------------------------------------------------------------------------
# compute_envelope — FPS lock + the seeded-bar-envelope-always-present rule
# ---------------------------------------------------------------------------

def test_compute_envelope_uses_module_fps():
    # audiogram_v2.FPS must match src.audiogram.FPS (30) — _band_amps()
    # indexes real-audio frames against that module-level constant
    # internally, so computing at a different rate would silently desync
    # the envelope from the frames it's meant to drive.
    from src.audiogram import FPS as PILLOW_FPS
    assert audiogram_v2.FPS == PILLOW_FPS == 30


def test_compute_envelope_falls_back_to_none_amps_with_no_audio(monkeypatch):
    monkeypatch.setattr(audiogram_v2, "_duration", lambda clip: 2.0)
    monkeypatch.setattr(audiogram_v2, "_audio_pcm", lambda clip: None)
    amps, seed_bars, nframes, dur = audiogram_v2.compute_envelope("fake.mp4", seed=21)
    assert amps is None
    assert nframes == 60  # 2.0s * 30fps
    assert dur == 2.0
    assert len(seed_bars) == NBARS
    for h, bdur, delay in seed_bars:
        assert 0.0 < h <= 1.0
        assert bdur > 0
        assert delay >= 0.0


def test_compute_envelope_uses_real_audio_when_present(monkeypatch):
    import numpy as np

    monkeypatch.setattr(audiogram_v2, "_duration", lambda clip: 1.0)
    monkeypatch.setattr(audiogram_v2, "_audio_pcm", lambda clip: np.ones(22050, dtype=np.float32))
    fixed = np.full((30, NBARS), 0.42, dtype=np.float32)
    monkeypatch.setattr(audiogram_v2, "_band_amps", lambda pcm, sr, nframes: fixed)
    amps, seed_bars, nframes, _dur = audiogram_v2.compute_envelope("fake.mp4", seed=21)
    assert amps is not None
    assert amps.shape == (30, NBARS)
    assert len(seed_bars) == NBARS  # seed envelope shape always present, audio or not


# ---------------------------------------------------------------------------
# build_props — the shape render-cli.mjs's audiogram-v2 mode reads
# ---------------------------------------------------------------------------

def _patch_no_audio(monkeypatch, dur=6.0):
    monkeypatch.setattr(audiogram_v2, "_duration", lambda clip: dur)
    monkeypatch.setattr(audiogram_v2, "_audio_pcm", lambda clip: None)


def test_build_props_static_caption_shape(monkeypatch):
    _patch_no_audio(monkeypatch)
    props, notes, dur = audiogram_v2.build_props(
        "fake.mp4", words=[], series="kintsugi-heroes", brand_override=None,
        caption="The gold goes into the cracks", title="A story of repair",
        guest_name="Jane Doe", ep_label="EP 14", width=1920, height=1080,
        fps=30, safety="ok",
    )
    assert notes == []
    assert dur == 6.0
    assert props["caption"] == "The gold goes into the cracks"
    assert props["timedLines"] is None
    assert props["amps"] is None
    assert len(props["seedBars"]) == NBARS
    assert props["palette"] == {"bg": "#424530", "ink": "#FFF9ED", "accent": "#ED9A1F"}
    assert props["logoFile"].endswith("logo-primary-allwhite.png")
    assert props["eyebrow"] == "Kintsugi Heroes"
    assert props["title"] == "A story of repair"
    assert props["guestName"] == "Jane Doe"
    assert props["epLabel"] == "EP 14"
    assert props["safety"] == "ok"
    assert props["durationInFrames"] == 180  # 6.0s * 30fps
    assert props["width"] == 1920 and props["height"] == 1080


def test_build_props_timed_lines_override_static_caption(monkeypatch):
    _patch_no_audio(monkeypatch, dur=8.0)
    words = [
        {"text": "you", "start": 0.0, "end": 0.4},
        {"text": "are", "start": 0.4, "end": 0.8},
        {"text": "not", "start": 0.8, "end": 1.2},
        {"text": "broken", "start": 1.2, "end": 1.8},
    ]
    props, _notes, _dur = audiogram_v2.build_props(
        "fake.mp4", words=words, series="kintsugi-heroes", brand_override=None,
        caption="a static line that should be dropped", title=None,
        guest_name=None, ep_label=None, width=1080, height=1080, fps=30, safety="ok",
    )
    assert props["caption"] is None            # timed lines take priority
    assert props["timedLines"] is not None
    assert props["timedLines"][0]["text"] == "you are not broken"


def test_build_props_brand_override_falls_back_per_key(monkeypatch):
    _patch_no_audio(monkeypatch)
    props, notes, _dur = audiogram_v2.build_props(
        "fake.mp4", words=[], series="golden-threads",
        brand_override={"bg": "#111122", "seed": "not-a-number"},
        caption="cap", title=None, guest_name=None, ep_label=None,
        width=1080, height=1080, fps=30, safety="review",
    )
    assert props["palette"]["bg"] == "#111122"       # override applied
    assert "brand seed invalid" in notes[0]           # bad key fell back, noted
    assert props["safety"] == "review"


def test_build_props_reuses_seeded_bar_count_across_calls(monkeypatch):
    _patch_no_audio(monkeypatch)
    props, _notes, _dur = audiogram_v2.build_props(
        "fake.mp4", words=[], series="grit-diaries", brand_override=None,
        caption="cap", title=None, guest_name=None, ep_label=None,
        width=1920, height=1080, fps=30, safety="ok",
    )
    assert len(props["seedBars"]) == NBARS
    for bar in props["seedBars"]:
        assert set(bar.keys()) == {"h", "dur", "delay"}


# ---------------------------------------------------------------------------
# FORMAT_TO_FRAME — the CLI's --format mapping
# ---------------------------------------------------------------------------

def test_format_to_frame_matches_pillow_constants():
    from src.audiogram import LANDSCAPE, SQUARE, VERTICAL
    assert audiogram_v2.FORMAT_TO_FRAME["landscape"] == LANDSCAPE
    assert audiogram_v2.FORMAT_TO_FRAME["square"] == SQUARE
    assert audiogram_v2.FORMAT_TO_FRAME["vertical"] == VERTICAL


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
