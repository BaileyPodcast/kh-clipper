"""
Tests for the Shorts Motion Graphics Upgrade (KH-MGX-001, Wave 1): kinetic
captions, the highlight word, face-aware placement and the CALM trauma-informed
preset. Pure ASS-string / config assertions — no ffmpeg, no MediaPipe, so this
runs anywhere.

    python -m pytest tests/test_caption_kinetic.py
    python tests/test_caption_kinetic.py        # also runs standalone (no pytest)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import brand, caption

ANIM = brand.ANIMATION


def _words(text, step=0.6, dur=0.5):
    return [{"text": w, "start": round(i * step, 2), "end": round(i * step + dur, 2)}
            for i, w in enumerate(text.split())]


# ---------------------------------------------------------------------------
# 1.5 — preset selection (the trauma-informed gate)
# ---------------------------------------------------------------------------

def test_preset_for_ok_is_standard():
    name, preset = caption._preset_for("ok")
    assert name == "standard"
    assert preset["pop"] and preset["highlight"] and preset["punch_in"]


def test_preset_for_review_is_calm():
    name, preset = caption._preset_for("review")
    assert name == "calm"
    assert not preset["pop"] and not preset["highlight"] and not preset["punch_in"]
    assert preset["fade_ms"] > ANIM["presets"]["standard"]["fade_ms"]


def test_preset_defaults_to_standard_when_safety_missing():
    name, _ = caption._preset_for(None)
    assert name == "standard"


# ---------------------------------------------------------------------------
# 1.1 — word pop-in + active-word scale + line-change fade
# ---------------------------------------------------------------------------

def test_active_word_pops_in_under_standard_preset():
    words = _words("you are not broken")
    events = caption.caption_events(words, preset=ANIM["presets"]["standard"])
    assert len(events) == len(words)
    first = events[0]
    # active word: starts at pop_from_scale, transforms to active_scale over pop_ms
    assert f"\\fscx{ANIM['pop_from_scale']}\\fscy{ANIM['pop_from_scale']}" in first
    assert f"\\t(0,{ANIM['pop_ms']}," in first
    assert f"\\fscx{ANIM['active_scale']}\\fscy{ANIM['active_scale']}" in first


def test_calm_preset_has_no_scale_or_pop_tags():
    words = _words("you are not broken")
    events = caption.caption_events(words, preset=ANIM["presets"]["calm"])
    for ev in events:
        assert "\\fscx" not in ev
        assert "\\t(" not in ev


def test_line_change_fades_in_only_the_first_event_of_each_line():
    # max_words_per_line is 4, so 8 words -> two lines, two "first" events.
    words = _words("one two three four five six seven eight")
    events = caption.caption_events(words, preset=ANIM["presets"]["standard"])
    fade_tag = f"\\fad({ANIM['presets']['standard']['fade_ms']},0)"
    faded = [e for e in events if fade_tag in e]
    assert len(faded) == 2                    # exactly the two line-openers
    # the first event of the SECOND line is the 5th event (index 4)
    assert fade_tag in events[4]
    assert fade_tag not in events[1]           # a mid-line word change never fades


def test_calm_preset_uses_its_own_longer_fade():
    words = _words("one two three four five")
    events = caption.caption_events(words, preset=ANIM["presets"]["calm"])
    assert f"\\fad({ANIM['presets']['calm']['fade_ms']},0)" in events[0]


# ---------------------------------------------------------------------------
# 1.2 — highlight_word emphasis
# ---------------------------------------------------------------------------

def test_highlight_word_renders_gold_and_oversized_even_when_not_active():
    words = _words("you are not broken you are still here")
    events = caption.caption_events(words, highlight_word="broken",
                                    preset=ANIM["presets"]["standard"])
    # "broken" is word index 3 (0-based); its OWN active event (index 3) pops in
    # to the highlight scale; every OTHER event where it's on screen (same line,
    # i.e. events 0-3) must still show it gold + oversized, not active-scaled.
    non_active_hit = events[0]                 # active word here is "you" (i=0)
    assert f"\\c{caption._c(brand.CAPTION['active_colour'])}" in non_active_hit
    assert f"\\fscx{ANIM['highlight_scale']}\\fscy{ANIM['highlight_scale']}" in non_active_hit
    assert "broken" in non_active_hit


def test_highlight_word_active_pops_to_highlight_scale_not_active_scale():
    words = _words("you are not broken")
    events = caption.caption_events(words, highlight_word="broken",
                                    preset=ANIM["presets"]["standard"])
    active_event = events[3]                   # "broken" is the active word here
    assert f"\\t(0,{ANIM['pop_ms']},\\fscx{ANIM['highlight_scale']}\\fscy{ANIM['highlight_scale']})" \
        in active_event


def test_highlight_word_matches_punctuation_insensitively():
    words = _words("hope, is not lost.")
    events = caption.caption_events(words, highlight_word="hope",
                                    preset=ANIM["presets"]["standard"])
    assert f"\\fscx{ANIM['highlight_scale']}" in events[0]


def test_calm_preset_disables_highlight_word_guard():
    # KH-MGX-001 1.2's guard: a review clip's highlight word never gets pulled
    # out for emphasis (the CALM preset covers it).
    words = _words("you are not broken")
    events = caption.caption_events(words, highlight_word="broken",
                                    preset=ANIM["presets"]["calm"])
    for ev in events:
        assert "\\fscx" not in ev              # no scale tags anywhere under CALM


# ---------------------------------------------------------------------------
# 1.3 — face-aware caption / banner placement
# ---------------------------------------------------------------------------

def test_caption_band_defaults_without_faceband():
    assert caption._band_for_captions(None) == ANIM["caption_bands"]["default_margin_v_px"]
    assert caption._band_for_captions({}) == ANIM["caption_bands"]["default_margin_v_px"]


def test_caption_band_raises_for_a_low_face():
    low = {"top": 0.30, "bottom": 0.70}
    assert caption._band_for_captions(low) == ANIM["caption_bands"]["raised_margin_v_px"]


def test_caption_band_stays_default_for_a_normal_face():
    normal = {"top": 0.25, "bottom": 0.55}
    assert caption._band_for_captions(normal) == ANIM["caption_bands"]["default_margin_v_px"]


def test_banner_band_drops_for_a_high_face():
    high = {"top": 0.05, "bottom": 0.40}
    assert caption._band_for_banner(high) == ANIM["banner_bands"]["mid_margin_v_px"]


def test_banner_band_stays_default_for_a_normal_face():
    normal = {"top": 0.25, "bottom": 0.55}
    assert caption._band_for_banner(normal) == ANIM["banner_bands"]["default_margin_v_px"]


def test_build_ass_style_line_reflects_the_chosen_band():
    import tempfile
    words = _words("hello there friend")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "test.ass")
        caption.build_ass(words, 5.0, "shorts", path,
                          faceband={"top": 0.30, "bottom": 0.70})
        content = open(path).read()
    raised = ANIM["caption_bands"]["raised_margin_v_px"]
    assert f",60,60,{raised}\n" in content or f",60,60,{raised}\r\n" in content


def test_load_faceband_missing_file_returns_none():
    assert caption._load_faceband("/no/such/clip.mp4") is None


# ---------------------------------------------------------------------------
# 1.4 — punch-in
# ---------------------------------------------------------------------------

def test_punch_in_filter_alternates_bias_by_direction():
    left = caption._punch_in_filter(10.0, (1080, 1920), 30.0, "left")
    right = caption._punch_in_filter(10.0, (1080, 1920), 30.0, "right")
    assert "iw*0.44" in left
    assert "iw*0.56" in right
    assert "zoompan=" in left and "d=1" in left
    assert str(ANIM["punch_in"]["end_scale"]) in left     # zooms to the configured cap


def test_punch_in_filter_none_on_bad_inputs():
    assert caption._punch_in_filter(0, (1080, 1920), 30.0, "left") is None
    assert caption._punch_in_filter(10.0, (1080, 1920), 0, "left") is None


def test_finish_alternates_punch_in_direction_by_clip_index(monkeypatch):
    # Lock down that finish() picks direction from clip_index without needing
    # ffmpeg — patch the pieces finish() calls before it shells out.
    calls = []

    def fake_punch(duration, frame, fps, direction):
        calls.append(direction)
        return None                            # skip the real zoompan string
    monkeypatch.setattr(caption, "_punch_in_filter", fake_punch)
    monkeypatch.setattr(caption, "_duration", lambda p: 5.0)
    monkeypatch.setattr(caption, "_fps", lambda p: 30.0)

    class _Boom(Exception):
        pass

    def fake_build_ass(*a, **k):
        raise _Boom("stop before ffmpeg")
    monkeypatch.setattr(caption, "build_ass", fake_build_ass)

    for i in (0, 1, 2, 3):
        try:
            caption.finish("clip.mp4", [], "out", clip_index=i)
        except _Boom:
            pass
    assert calls == ["left", "right", "left", "right"]


# ---------------------------------------------------------------------------
# Hook banner timing: frame 1 start, 3-second minimum hold
# ---------------------------------------------------------------------------

def test_banner_starts_on_frame_1_and_keeps_the_fade():
    events = caption.banner_events("He stopped hiding", 30.0)
    assert len(events) == 1
    assert ",0:00:00.00," in events[0]                # t0 = 0.0, frame 1
    assert "\\fad(350,350)" in events[0]


def test_banner_holds_at_least_3_seconds_on_a_short_clip():
    # duration 4s -> 4 * 0.55 = 2.2, clamped up to the 3.0 floor
    events = caption.banner_events("He stopped hiding", 4.0)
    assert ",0:00:03.00," in events[0]


def test_banner_caps_at_5_seconds_on_a_long_clip():
    events = caption.banner_events("He stopped hiding", 30.0)
    assert ",0:00:05.00," in events[0]


def test_banner_window_scales_between_floor_and_cap():
    # duration 7s -> 7 * 0.55 = 3.85, inside [3, 5]
    events = caption.banner_events("He stopped hiding", 7.0)
    assert ",0:00:03.85," in events[0]


# ---------------------------------------------------------------------------
# Final export encode pinned (never ffmpeg's default CRF 23)
# ---------------------------------------------------------------------------

def test_final_encode_is_pinned_crf_18_faststart():
    e = caption.FINAL_ENCODE
    assert e[e.index("-c:v") + 1] == "libx264"
    assert e[e.index("-preset") + 1] == "medium"
    assert e[e.index("-crf") + 1] == "18"
    assert e[e.index("-pix_fmt") + 1] == "yuv420p"
    assert "+faststart" in e[e.index("-movflags") + 1]


# ---------------------------------------------------------------------------
# Banner placement: rests about a third down the frame, drop rule still wins
# ---------------------------------------------------------------------------

def test_banner_default_band_sits_near_the_upper_third():
    default = ANIM["banner_bands"]["default_margin_v_px"]
    # Banner box (~110px for font 80 + pill padding) centred near 1920/3 = 640.
    assert abs((default + 55) - 1920 / 3) < 60


def test_banner_drop_band_stays_below_the_default():
    b = ANIM["banner_bands"]
    assert b["mid_margin_v_px"] > b["default_margin_v_px"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
        except TypeError:
            continue            # needs pytest fixtures (monkeypatch) — skip standalone
        print(f"ok  {fn.__name__}")
        passed += 1
    print(f"\n{passed} passed")
