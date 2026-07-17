"""
Unit tests for the 16:9 landscape audiogram path (KH-VRL-001 Wave 1).

Covers the pure, network-free parts: the LANDSCAPE layout branch, per-key brand
resolution and fallback, transcript window slicing + caption line grouping, and
the action="video" payload validation. No Modal, no Supabase, no ffmpeg needed
(the static layer is composed with PIL only).

    python -m pytest tests/test_audiogram_landscape.py
"""
import importlib.util
import os
import sys
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import audiogram


# ----------------------------------------------------------------------
# LANDSCAPE constant + static layer
# ----------------------------------------------------------------------
def _build(pal, **kw):
    args = {"caption": "The gold goes into the cracks", "title": "A story of repair",
            "guest": "Jane Doe", "eyebrow": "Kintsugi Heroes", "ep_label": "EP 14"}
    args.update(kw)
    return audiogram._build_static(audiogram.LANDSCAPE, pal, ROOT, **args)


def test_landscape_constant_is_16_9():
    assert audiogram.LANDSCAPE == (1920, 1080)


def test_static_layer_is_1920_by_1080():
    pal = audiogram.palette_for("kintsugi-heroes")
    static, layout = _build(pal)
    assert static.shape == (1080, 1920, 3)
    # The waveform centre and progress bar must sit inside the frame.
    assert 0 < layout["eq_center_y"] < 1080
    assert 0 < layout["prog_y"] < 1080
    assert layout["prog_x"] + layout["prog_w"] <= 1920


def test_static_layer_applies_brand_bg_override():
    pal, _texts, notes = audiogram.resolve_brand("kintsugi-heroes", {"bg": "#102030"})
    assert notes == []
    static, _ = _build(pal)
    # Bottom-left corner (outside the border inset, away from the top radial
    # highlight) should be the override colour, within a small tolerance.
    r, g, b = static[1075, 5]
    assert abs(int(r) - 0x10) <= 8 and abs(int(g) - 0x20) <= 8 and abs(int(b) - 0x30) <= 8


def test_timed_caption_mode_reserves_the_caption_slot():
    pal = audiogram.palette_for("kintsugi-heroes")
    _static, layout = _build(pal, timed_captions=True)
    slot = layout["cap_slot"]
    assert slot["y"] > layout["eq_center_y"]           # slot sits under the waveform
    assert slot["max_w"] > audiogram.LANDSCAPE[0] * 0.5  # wide frame gets a wide slot
    assert layout["cap_font"] is not None


def test_square_and_vertical_static_layers_are_unchanged_shape():
    # The wide branch must not disturb the existing formats.
    pal = audiogram.palette_for("golden-threads")
    sq, _ = audiogram._build_static(audiogram.SQUARE, pal, ROOT, caption="x",
                                    title="t", guest=None, eyebrow="E", ep_label=None)
    vt, _ = audiogram._build_static(audiogram.VERTICAL, pal, ROOT, caption="x",
                                    title="t", guest=None, eyebrow="E", ep_label=None)
    assert sq.shape == (1080, 1080, 3)
    assert vt.shape == (1920, 1080, 3)


# ----------------------------------------------------------------------
# Brand resolution: per-key override + per-key fallback, never fails
# ----------------------------------------------------------------------
def test_resolve_brand_full_override():
    brand = {"bg": "#111122", "ink": "#EEDDCC", "wave": "#ABCDEF",
             "logo_colourway": "black", "seed": 5, "eyebrow": "Voices",
             "display_name": "Voices of the Valley", "tagline": "Real stories"}
    pal, texts, notes = audiogram.resolve_brand("kintsugi-heroes", brand)
    assert pal["bg"] == "#111122" and pal["ink"] == "#EEDDCC"
    assert pal["accent"] == "#ABCDEF"                  # wave maps to accent
    assert pal["logo"] == "black" and pal["seed"] == 5
    assert texts["eyebrow"] == "Voices"
    assert texts["display_name"] == "Voices of the Valley"
    assert texts["tagline"] == "Real stories"
    assert notes == []


def test_resolve_brand_partial_falls_back_per_key():
    base = audiogram.palette_for("grit-diaries")
    pal, texts, notes = audiogram.resolve_brand("grit-diaries", {"bg": "#333344"})
    assert pal["bg"] == "#333344"
    assert pal["ink"] == base["ink"]                   # untouched keys keep series values
    assert pal["accent"] == base["accent"]
    assert pal["logo"] == base["logo"] and pal["seed"] == base["seed"]
    assert texts["eyebrow"] == "Grit Diaries"
    assert notes == []


def test_resolve_brand_malformed_values_fall_back_with_notes():
    base = audiogram.palette_for("kintsugi-heroes")
    brand = {"bg": "not-a-colour", "ink": 12345, "seed": "lots",
             "logo_colourway": "sepia", "eyebrow": "   "}
    pal, texts, notes = audiogram.resolve_brand("kintsugi-heroes", brand)
    assert pal == base                                  # every bad key fell back
    assert texts["eyebrow"] == "Kintsugi Heroes"
    assert len(notes) >= 4


def test_resolve_brand_non_dict_block_never_fails():
    base = audiogram.palette_for("kintsugi-heroes")
    for junk in ("gold", 7, ["bg"], True):
        pal, _texts, notes = audiogram.resolve_brand("kintsugi-heroes", junk)
        assert pal == base
        assert notes


def test_resolve_brand_unknown_series_falls_back_to_kintsugi_heroes():
    pal, texts, _ = audiogram.resolve_brand("brand-new-series", None)
    assert pal == audiogram.PALETTES[audiogram.FALLBACK_SERIES]
    assert texts["eyebrow"] == "brand-new-series"


# ----------------------------------------------------------------------
# Window slicing + caption grouping (pure functions)
# ----------------------------------------------------------------------
def _w(text, start, end):
    return {"text": text, "start": start, "end": end}


def test_window_words_slices_and_rebases():
    words = [_w("before", 8.0, 9.5), _w("edge", 9.5, 10.5), _w("in", 11.0, 11.4),
             _w("out", 40.0, 40.5), _w("after", 41.0, 42.0)]
    out = audiogram.window_words(words, 10, 40)
    assert [w["text"] for w in out] == ["edge", "in"]   # overlap-inclusive at the edge
    assert out[0]["start"] == 0.0                       # clamped to the window start
    assert abs(out[1]["start"] - 1.0) < 1e-9            # 11.0 - 10
    assert out[0]["end"] <= 30.0


def test_window_words_drops_junk_entries():
    words = [_w("ok", 12, 13), {"text": "no-times"}, {"start": 14, "end": 15},
             _w("  ", 16, 17), "not-a-dict", _w("fine", 18, 19)]
    out = audiogram.window_words(words, 10, 40)
    assert [w["text"] for w in out] == ["ok", "fine"]


def test_group_caption_lines_splits_on_duration_and_word_count():
    # 12 words, 0.5s each: the 7-word cap splits first, then duration.
    words = [_w(f"w{i}", i * 0.5, i * 0.5 + 0.4) for i in range(12)]
    chunks = audiogram.group_caption_lines(words, max_sec=3.5, max_words=7)
    assert len(chunks) == 2
    assert chunks[0]["text"].split() == [f"w{i}" for i in range(7)]
    assert chunks[0]["start"] == 0.0
    assert chunks[1]["start"] == 3.5
    # A slow talker splits on duration instead.
    slow = [_w(f"s{i}", i * 2.0, i * 2.0 + 1.8) for i in range(4)]
    chunks = audiogram.group_caption_lines(slow, max_sec=3.5, max_words=7)
    assert len(chunks) >= 2
    for c in chunks:
        assert c["end"] > c["start"]


def test_group_caption_lines_empty_input():
    assert audiogram.group_caption_lines([]) == []
    assert audiogram.group_caption_lines(None) == []


# ----------------------------------------------------------------------
# action="video" payload validation (worker/app.py, imported with Modal and
# fastapi stubbed out so no cloud deps are needed)
# ----------------------------------------------------------------------
def _load_worker_app():
    for mod in ("modal", "fastapi"):
        sys.modules.setdefault(mod, MagicMock())
    spec = importlib.util.spec_from_file_location(
        "worker_app_under_test", os.path.join(ROOT, "worker", "app.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(**over):
    p = {"action": "video", "job_id": "3e1c2f9a-0000-4000-8000-000000000000",
         "url": "https://www.youtube.com/watch?v=iTX6b2Z01II",
         "series": "kintsugi-heroes", "format": "audiogram_landscape",
         "start_sec": 30, "end_sec": 105}
    p.update(over)
    return p


def test_valid_video_payload_passes():
    app = _load_worker_app()
    assert app.validate_video_payload(_payload()) is None


def test_missing_required_fields_are_rejected():
    app = _load_worker_app()
    for field in ("job_id", "url", "series"):
        p = _payload()
        del p[field]
        assert field in app.validate_video_payload(p)
        p[field] = "   "
        assert field in app.validate_video_payload(p)


def test_unknown_format_is_rejected():
    app = _load_worker_app()
    assert "format" in app.validate_video_payload(_payload(format="audiogram_square"))
    p = _payload()
    del p["format"]
    assert "format" in app.validate_video_payload(p)


def test_bad_window_is_rejected():
    app = _load_worker_app()
    v = app.validate_video_payload
    assert v(_payload(start_sec="30")) is not None       # non-int
    assert v(_payload(start_sec=True)) is not None       # bool is not an int here
    assert v(_payload(start_sec=50, end_sec=40)) is not None    # inverted
    assert v(_payload(start_sec=50, end_sec=50)) is not None    # empty
    assert v(_payload(start_sec=-5, end_sec=40)) is not None    # negative
    assert v(_payload(start_sec=0, end_sec=5)) is not None      # under 10s
    assert v(_payload(start_sec=0, end_sec=300)) is not None    # over 180s
    assert v(_payload(start_sec=0, end_sec=180)) is None        # max window ok


def test_non_dict_payload_is_rejected():
    app = _load_worker_app()
    assert app.validate_video_payload(None) is not None
    assert app.validate_video_payload("nope") is not None
