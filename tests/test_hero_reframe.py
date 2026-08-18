"""
Unit tests for hero-locked reframing on split-screen sources (Stage 4).

Covers the pure logic added for the split-screen fix, without MediaPipe or ffmpeg:
  - speech_windows(): diarized transcript words -> clip-relative hero/other windows
  - face._diarized_pick(): the hero is the face whose mouth moves when the
    transcript says the hero talks
  - face._tile_bounds(): a side-by-side composite yields the hero's tile bounds
  - reframe._x_range()/_follow_vf()/_pan_keyframes(): the crop never crosses the
    seam into the other person's tile
  - reframe(): the ambiguous fallback renders fit-both, not a centre-crop

    python -m pytest tests/test_hero_reframe.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import face, reframe


# --- speech_windows: transcript -> clip-relative speaking windows -----------------

def _w(start, end, speaker):
    return {"text": "x", "start": start, "end": end, "speaker": speaker}


def test_speech_windows_rebases_and_splits_by_speaker():
    words = [_w(10.0, 10.4, 1), _w(10.5, 11.0, 1),   # guest, merges (gap 0.1)
             _w(12.0, 12.5, 0),                       # host
             _w(14.0, 14.5, 1)]                       # guest, separate (gap 3.0)
    sw = reframe.speech_windows(words, 10.0, 15.0, guest_speaker=1)
    assert sw["guest"] == [(0.0, 1.0), (4.0, 4.5)]
    assert sw["others"] == [(2.0, 2.5)]


def test_speech_windows_clips_to_the_cut():
    # A word straddling the clip start is trimmed to t=0; words outside are dropped.
    words = [_w(9.5, 10.5, 1), _w(20.0, 21.0, 1)]
    sw = reframe.speech_windows(words, 10.0, 15.0, guest_speaker=1)
    assert sw["guest"] == [(0.0, 0.5)]


def test_speech_windows_none_without_diarization():
    assert reframe.speech_windows([_w(1, 2, None)], 0, 10, guest_speaker=None) is None
    assert reframe.speech_windows([_w(1, 2, None)], 0, 10, guest_speaker=1) is None
    # Guest never speaks inside the clip -> no signal to give.
    assert reframe.speech_windows([_w(1, 2, 0)], 0, 10, guest_speaker=1) is None


# --- _diarized_pick: lip motion matched against the transcript --------------------

def _track(motion_by_frame, cx=300):
    frames = {fi: {"cx": cx, "area": 1.0, "bbox": (cx - 50, 100, 100, 100)}
              for fi in motion_by_frame}
    return {"frames": frames, "last_cx": cx, "motion": dict(motion_by_frame)}


def test_diarized_pick_follows_the_hero_not_the_talker_of_the_moment():
    # Frames 0-5 = guest windows, 6-11 = host windows. The hero's mouth moves in the
    # guest windows; the host's moves in the host windows. Audio-gating alone would
    # call this ambiguous (both "speak"); diarization resolves it.
    hero = _track({fi: 5.0 for fi in range(0, 6)}, cx=300)
    host = _track({fi: 5.0 for fi in range(6, 12)}, cx=1500)
    gf = face._window_flags([(0.0, 5 / face.SAMPLE_FPS)], 12)
    of = face._window_flags([(6 / face.SAMPLE_FPS, 11 / face.SAMPLE_FPS)], 12)
    assert face._diarized_pick([host, hero], gf, of) is hero


def test_diarized_pick_none_when_both_faces_track_guest_windows():
    a = _track({fi: 5.0 for fi in range(0, 6)}, cx=300)
    b = _track({fi: 4.5 for fi in range(0, 6)}, cx=1500)
    gf = face._window_flags([(0.0, 5 / face.SAMPLE_FPS)], 12)
    assert face._diarized_pick([a, b], gf, []) is None


def test_diarized_pick_none_without_windows():
    assert face._diarized_pick([_track({0: 1.0})], [], []) is None
    assert face._diarized_pick([_track({0: 1.0})], None, None) is None


# --- _tile_bounds: the hero's half of a split-screen composite --------------------

def _frames_at(cx, n=10):
    return {fi: {"cx": cx, "area": 1.0, "bbox": (cx - 50, 100, 100, 100)}
            for fi in range(n)}


def test_tile_bounds_split_screen_left_guest():
    guest = _frames_at(480)          # left tile of a 1920 composite
    host = {"frames": _frames_at(1440), "last_cx": 1440}
    lo, hi = face._tile_bounds(guest, [host], width=1920)
    assert lo == 0.0 and hi == 960.0     # midpoint between the two medians


def test_tile_bounds_none_when_faces_share_a_camera():
    # Two people 300px apart on one 1920 camera is NOT a split screen (< TILE_SEP).
    guest = _frames_at(800)
    host = {"frames": _frames_at(1100), "last_cx": 1100}
    assert face._tile_bounds(guest, [host], width=1920) is None


def test_tile_bounds_none_without_others():
    assert face._tile_bounds(_frames_at(480), [], width=1920) is None


# --- crop clamping inside the tile ------------------------------------------------

def test_follow_vf_stays_inside_the_guest_tile():
    # Guest at x=900, just left of a seam at 960. An unbounded crop centred on 900
    # would cross into the host's tile; the bounded one must not.
    crop_w = round(1080 * 1080 / 1920)   # 608
    vf = reframe._follow_vf(1080, 1920, 1920, 1080, 900, bounds=(0, 960))
    x = int(vf.split(f"crop={crop_w}:1080:")[1].split(",")[0].split(":")[0])
    assert x + crop_w <= 960             # never crosses the seam
    assert x >= 0


def test_follow_vf_centres_on_a_tile_narrower_than_the_crop():
    # A 500px tile can't contain a 608px crop: centre the crop on the tile instead.
    crop_w = round(1080 * 1080 / 1920)
    vf = reframe._follow_vf(1080, 1920, 1920, 1080, 250, bounds=(0, 500))
    x = int(vf.split(f"crop={crop_w}:1080:")[1].split(",")[0].split(":")[0])
    assert abs((x + crop_w / 2) - 250) <= crop_w / 2   # tile centre inside the crop


def test_pan_keyframes_respect_tile_bounds():
    crop_w = round(1080 * 1080 / 1920)
    # Guest drifts toward the seam at 960; every keyframe must stay in the tile.
    track = [(t / 6.0, 700 + t * 20) for t in range(24)]
    kf = reframe._pan_keyframes(track, crop_w, 1920, bounds=(0, 960))
    assert kf and all(0 <= x <= 960 - crop_w for _, x in kf)


# --- ambiguous fallback renders fit-both, flagged for review ----------------------

def test_ambiguous_renders_fit_both_and_flags_review(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(face, "detector_available", lambda: True)
    monkeypatch.setattr(face, "analyze", lambda src, **kw: {
        "width": 1920, "height": 1080, "n_frames": 10, "multi_ratio": 1.0,
        "guest_cx": None, "mode": "ambiguous", "track": None, "people": 2,
        "face_band": None, "bounds": None})
    monkeypatch.setattr(reframe, "_run_fit", lambda w, h, s, o: calls.setdefault("fit", True))
    monkeypatch.setattr(reframe, "_run_ff", lambda vf, s, o: calls.setdefault("ff", True))
    flag = reframe.reframe(str(tmp_path / "in.mp4"), str(tmp_path / "out.mp4"))
    assert flag == "review"
    assert calls.get("fit") and not calls.get("ff")   # fit-both, never a centre-crop


def test_follow_passes_speech_windows_to_analyze(monkeypatch, tmp_path):
    seen = {}

    def fake_analyze(src, guest_windows=None, other_windows=None):
        seen["guest"] = guest_windows
        seen["others"] = other_windows
        return {"width": 1920, "height": 1080, "n_frames": 10, "multi_ratio": 1.0,
                "guest_cx": 480, "mode": "follow", "track": None, "people": 2,
                "face_band": None, "bounds": (0, 960)}

    monkeypatch.setattr(face, "detector_available", lambda: True)
    monkeypatch.setattr(face, "analyze", fake_analyze)
    monkeypatch.setattr(reframe, "_run_ff", lambda vf, s, o: None)
    speech = {"guest": [(0.0, 3.0)], "others": [(3.0, 4.0)]}
    flag = reframe.reframe(str(tmp_path / "in.mp4"), str(tmp_path / "out.mp4"),
                           speech=speech)
    assert flag == "ok"
    assert seen == {"guest": [(0.0, 3.0)], "others": [(3.0, 4.0)]}


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
