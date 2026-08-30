"""
Episode QC, file side (src/qc.py) plus the action="episode_qc" payload validator
in worker/app.py. Every ffmpeg/ffprobe call is a pure builder plus a pure parser,
so nothing here runs ffmpeg, touches the network or imports Modal for real.

The stderr fixtures below are the real shape ffmpeg emits, because a parser
tested against invented output proves nothing about the file it will meet.

    python -m pytest tests/test_qc.py
"""
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)

from src import qc  # noqa: E402


# ----------------------------------------------------------------------
# Command builders.
# ----------------------------------------------------------------------
def test_blackdetect_command_scans_video_only_and_writes_nothing():
    cmd = qc.build_blackdetect_command("master.mp4")
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-i") + 1] == "master.mp4"
    assert cmd[cmd.index("-vf") + 1] == f"blackdetect=d={qc.BLACK_MIN_DUR}:pix_th=0.1"
    assert "-an" in cmd                       # no audio decode on a picture pass
    assert cmd[-3:] == ["-f", "null", "-"]    # measures, never writes a file


def test_freezedetect_command_uses_the_configured_hold():
    cmd = qc.build_freezedetect_command("master.mp4")
    assert cmd[cmd.index("-vf") + 1] == f"freezedetect=n=-60dB:d={qc.FREEZE_MIN_DUR}"


def test_silencedetect_command_defaults_and_overrides():
    cmd = qc.build_silencedetect_command("master.mp4")
    assert cmd[cmd.index("-af") + 1] == "silencedetect=noise=-45dB:d=0.6"
    assert "-vn" in cmd                       # no picture decode on an audio pass
    loud = qc.build_silencedetect_command("master.mp4", noise_db=-30, min_dur=1.5)
    assert loud[loud.index("-af") + 1] == "silencedetect=noise=-30dB:d=1.5"


def test_ebur128_command_asks_for_true_peak():
    cmd = qc.build_ebur128_command("master.mp4")
    # Sample peak misses inter-sample overs, which is exactly what the platforms
    # measure, so the true peak is not optional here.
    assert cmd[cmd.index("-af") + 1] == "ebur128=peak=true"


def test_astats_command_measures_the_whole_file():
    cmd = qc.build_astats_command("master.mp4")
    assert cmd[cmd.index("-af") + 1] == "astats=reset=0"


def test_probe_command_asks_for_streams_and_format_as_json():
    cmd = qc.build_probe_command("master.mp4")
    assert cmd[0] == "ffprobe"
    assert "-show_streams" in cmd and "-show_format" in cmd
    assert cmd[cmd.index("-of") + 1] == "json"
    assert cmd[-1] == "master.mp4"


def test_frame_sample_command_seeks_instead_of_decoding_from_zero():
    cmd = qc.build_frame_sample_command("master.mp4", 120.5, frames=3)
    assert cmd[cmd.index("-read_intervals") + 1] == "120.5%+#3"
    assert cmd[cmd.index("-select_streams") + 1] == "v:0"


# ----------------------------------------------------------------------
# blackdetect / freezedetect / silencedetect parsers.
# ----------------------------------------------------------------------
BLACKDETECT_STDERR = """\
[blackdetect @ 0x55a0f1c2b400] black_start:0 black_end:1.2 black_duration:1.2
frame= 1200 fps=250 q=-0.0 size=N/A time=00:00:48.00 bitrate=N/A speed=  10x
[blackdetect @ 0x55a0f1c2b400] black_start:1435.29 black_end:1436.71 black_duration:1.41667
"""


def test_parse_blackdetect_reads_every_run():
    assert qc.parse_blackdetect(BLACKDETECT_STDERR) == [(0.0, 1.2), (1435.29, 1436.71)]


def test_parse_blackdetect_on_a_clean_episode():
    assert qc.parse_blackdetect("frame= 1200 fps=250 q=-0.0\n") == []
    assert qc.parse_blackdetect("") == []
    assert qc.parse_blackdetect(None) == []


FREEZEDETECT_STDERR = """\
[freezedetect @ 0x5580e4a1b200] lavfi.freezedetect.freeze_start: 320.44
[freezedetect @ 0x5580e4a1b200] lavfi.freezedetect.freeze_duration: 4.2
[freezedetect @ 0x5580e4a1b200] lavfi.freezedetect.freeze_end: 324.64
"""


def test_parse_freezedetect_pairs_start_and_end():
    assert qc.parse_freezedetect(FREEZEDETECT_STDERR) == [(320.44, 324.64)]


def test_parse_freezedetect_keeps_a_freeze_that_runs_to_the_end_of_the_file():
    stderr = "[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: 3600.5\n"
    assert qc.parse_freezedetect(stderr) == [(3600.5, None)]


SILENCEDETECT_STDERR = """\
[silencedetect @ 0x5601a2b3c400] silence_start: 754.213
[silencedetect @ 0x5601a2b3c400] silence_end: 755.031 | silence_duration: 0.818
[silencedetect @ 0x5601a2b3c400] silence_start: 2210.5
[silencedetect @ 0x5601a2b3c400] silence_end: 2214.1 | silence_duration: 3.6
"""


def test_parse_silencedetect_reads_each_gap():
    assert qc.parse_silencedetect(SILENCEDETECT_STDERR) == [(754.213, 755.031),
                                                            (2210.5, 2214.1)]


def test_parse_silencedetect_keeps_silence_running_to_the_end():
    stderr = "[silencedetect @ 0x1] silence_start: 3599.0\n"
    assert qc.parse_silencedetect(stderr) == [(3599.0, None)]


# ----------------------------------------------------------------------
# ebur128: the true peak must not be confused with the sample peak.
# ----------------------------------------------------------------------
EBUR128_STDERR = """\
[Parsed_ebur128_0 @ 0x55d3f2a10c40] Summary:

  Integrated loudness:
    I:         -16.4 LUFS
    Threshold: -26.6 LUFS

  Loudness range:
    LRA:         7.4 LU
    Threshold: -36.7 LUFS
    LRA low:   -21.0 LUFS
    LRA high:  -13.6 LUFS

  Sample peak:
    Peak:       -0.5 dBFS

  True peak:
    Peak:       -0.3 dBFS
"""


def test_parse_ebur128_reads_the_summary():
    m = qc.parse_ebur128(EBUR128_STDERR)
    assert m["integrated_lufs"] == -16.4
    assert m["lra"] == 7.4
    # -0.3 is the TRUE peak; -0.5 is the sample peak two lines above it.
    assert m["true_peak_dbtp"] == -0.3


def test_parse_ebur128_without_a_sample_peak_block():
    stderr = EBUR128_STDERR.replace("  Sample peak:\n    Peak:       -0.5 dBFS\n\n", "")
    assert qc.parse_ebur128(stderr)["true_peak_dbtp"] == -0.3


def test_parse_ebur128_on_silence_returns_none_not_a_guess():
    stderr = """\
  Integrated loudness:
    I:         -inf LUFS
"""
    m = qc.parse_ebur128(stderr)
    assert m["integrated_lufs"] is None
    assert m["true_peak_dbtp"] is None
    assert m["lra"] is None


def test_parse_ebur128_on_empty_output():
    assert qc.parse_ebur128("") == {"integrated_lufs": None, "true_peak_dbtp": None,
                                    "lra": None}


# ----------------------------------------------------------------------
# astats.
# ----------------------------------------------------------------------
ASTATS_STDERR = """\
[Parsed_astats_0 @ 0x556a1b2c3400] Channel: 1
[Parsed_astats_0 @ 0x556a1b2c3400] DC offset: 0.000031
[Parsed_astats_0 @ 0x556a1b2c3400] Peak level dB: -3.114453
[Parsed_astats_0 @ 0x556a1b2c3400] Flat factor: 0.000000
[Parsed_astats_0 @ 0x556a1b2c3400] Peak count: 2
[Parsed_astats_0 @ 0x556a1b2c3400] Channel: 2
[Parsed_astats_0 @ 0x556a1b2c3400] Peak level dB: -0.045000
[Parsed_astats_0 @ 0x556a1b2c3400] Flat factor: 0.000000
[Parsed_astats_0 @ 0x556a1b2c3400] Peak count: 3
[Parsed_astats_0 @ 0x556a1b2c3400] Overall
[Parsed_astats_0 @ 0x556a1b2c3400] Peak level dB: -0.045000
[Parsed_astats_0 @ 0x556a1b2c3400] Flat factor: 0.000000
[Parsed_astats_0 @ 0x556a1b2c3400] Peak count: 5
"""


def test_parse_astats_prefers_the_overall_block():
    stats = qc.parse_astats(ASTATS_STDERR)
    assert stats["peak_level_db"] == -0.045
    assert stats["peak_count"] == 5
    assert stats["flat_factor"] == 0.0


def test_parse_astats_without_an_overall_block_takes_the_worst_channel():
    per_channel = ASTATS_STDERR.split("Overall")[0]
    stats = qc.parse_astats(per_channel)
    # Clipping on one channel is still clipping, so the loudest peak wins.
    assert stats["peak_level_db"] == -0.045


def test_parse_astats_on_empty_output():
    assert qc.parse_astats("") == {"peak_level_db": None, "flat_factor": None,
                                   "peak_count": None}


# ----------------------------------------------------------------------
# ffprobe.
# ----------------------------------------------------------------------
PROBE_JSON = json.dumps({
    "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
         "avg_frame_rate": "25/1", "r_frame_rate": "25/1", "start_time": "0.000000",
         "duration": "3611.720000"},
        {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000",
         "channels": 2, "start_time": "0.000000", "duration": "3611.690000"},
    ],
    "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "3611.720000",
               "size": "2481203344"},
})


def test_parse_probe_reads_the_facts_qc_needs():
    info = qc.parse_probe(PROBE_JSON)
    assert info["duration_sec"] == 3611.72
    assert info["size_bytes"] == 2481203344
    assert info["video"]["width"] == 1920 and info["video"]["height"] == 1080
    assert info["video"]["fps"] == 25.0
    assert info["audio"]["sample_rate"] == 48000
    assert info["audio"]["channels"] == 2
    assert "mp4" in info["container"]


def test_parse_probe_reads_a_fractional_frame_rate():
    data = json.loads(PROBE_JSON)
    data["streams"][0]["avg_frame_rate"] = "30000/1001"
    assert qc.parse_probe(json.dumps(data))["video"]["fps"] == 29.97


def test_parse_probe_survives_a_zero_frame_rate():
    data = json.loads(PROBE_JSON)
    data["streams"][0]["avg_frame_rate"] = "0/0"
    data["streams"][0]["r_frame_rate"] = "0/0"
    assert qc.parse_probe(json.dumps(data))["video"]["fps"] is None


def test_parse_probe_on_an_audio_only_file():
    data = {"streams": [{"codec_type": "audio", "codec_name": "aac",
                         "sample_rate": "44100", "channels": 1}],
            "format": {"format_name": "mp3", "duration": "60.0", "size": "100"}}
    info = qc.parse_probe(json.dumps(data))
    assert info["video"] is None
    assert info["audio"]["channels"] == 1


def test_parse_probe_on_unusable_output_returns_empty():
    assert qc.parse_probe("") == {}
    assert qc.parse_probe("not json") == {}
    assert qc.parse_probe(None) == {}


def test_probe_media_returns_empty_when_ffprobe_fails(monkeypatch):
    monkeypatch.setattr(qc, "run_capture", lambda *a, **k: (False, PROBE_JSON, ""))
    # A failed probe must never come back looking like a successful one.
    assert qc.probe_media("master.mp4") == {}


# ----------------------------------------------------------------------
# Resolution / frame rate sampling.
# ----------------------------------------------------------------------
def test_sample_offsets_spread_inside_the_file():
    offsets = qc.sample_offsets(3600, samples=5)
    assert len(offsets) == 5
    assert offsets[0] >= 5.0 and offsets[-1] <= 3595.0
    assert offsets == sorted(offsets)


def test_sample_offsets_on_an_unusable_duration():
    assert qc.sample_offsets(0) == []
    assert qc.sample_offsets(None) == []
    assert qc.sample_offsets("nope") == []


def _frames_json(width, height, start=100.0, step=0.04, n=5):
    frames = [{"width": width, "height": height,
               "best_effort_timestamp_time": f"{start + step * i:.6f}"}
              for i in range(n)]
    return json.dumps({"frames": frames})


def test_parse_frame_samples_reads_size_and_a_local_frame_rate():
    sample = qc.parse_frame_samples(_frames_json(1920, 1080, step=0.04))
    assert sample["width"] == 1920 and sample["height"] == 1080
    assert sample["fps"] == 25.0
    assert sample["frames"] == 5


def test_parse_frame_samples_on_unusable_output():
    assert qc.parse_frame_samples("") == {}
    assert qc.parse_frame_samples('{"frames": []}') == {}


def test_detect_resolution_changes_spots_a_spliced_in_clip(monkeypatch):
    calls = {"n": 0}

    def fake_run(cmd, timeout=None):
        calls["n"] += 1
        # The third sample point is a clip exported at 1280x720.
        if calls["n"] == 3:
            return True, _frames_json(1280, 720), ""
        return True, _frames_json(1920, 1080), ""

    monkeypatch.setattr(qc, "run_capture", fake_run)
    changes = qc.detect_resolution_changes("master.mp4", duration_sec=3600, samples=4)
    assert len(changes) == 1
    assert (changes[0]["width"], changes[0]["height"]) == (1280, 720)
    assert (changes[0]["baseline_width"], changes[0]["baseline_height"]) == (1920, 1080)


def test_detect_resolution_changes_on_a_consistent_file(monkeypatch):
    monkeypatch.setattr(qc, "run_capture",
                        lambda *a, **k: (True, _frames_json(1920, 1080), ""))
    assert qc.detect_resolution_changes("master.mp4", duration_sec=3600) == []


def test_detect_resolution_changes_never_raises_when_sampling_fails(monkeypatch):
    monkeypatch.setattr(qc, "run_capture", lambda *a, **k: (False, "", ""))
    assert qc.detect_resolution_changes("master.mp4", duration_sec=3600) == []


# ----------------------------------------------------------------------
# A/V sync.
# ----------------------------------------------------------------------
def test_av_sync_drift_measures_start_and_duration_gaps():
    probe = qc.parse_probe(PROBE_JSON)
    drift = qc.av_sync_drift("master.mp4", probe=probe)
    assert drift["start_offset_sec"] == 0.0
    assert drift["duration_delta_sec"] == 0.03


def test_av_sync_drift_reports_a_real_offset():
    data = json.loads(PROBE_JSON)
    data["streams"][1]["start_time"] = "0.480000"
    drift = qc.av_sync_drift("master.mp4", probe=qc.parse_probe(json.dumps(data)))
    assert drift["start_offset_sec"] == -0.48


def test_av_sync_drift_without_both_streams_reports_nothing_measured():
    audio_only = {"streams": [{"codec_type": "audio", "codec_name": "aac"}],
                  "format": {"duration": "60.0"}}
    assert qc.av_sync_drift("master.mp4", probe=qc.parse_probe(json.dumps(audio_only))) == {}


# ----------------------------------------------------------------------
# File identity.
# ----------------------------------------------------------------------
def test_md5_file_matches_hashlib(tmp_path):
    target = tmp_path / "master.mp4"
    payload = b"kintsugi" * 5000
    target.write_bytes(payload)
    assert qc.md5_file(str(target), chunk_bytes=1024) == hashlib.md5(payload).hexdigest()


def test_md5_file_on_a_missing_file_returns_none():
    assert qc.md5_file("/tmp/definitely-not-here-kh-qc.mp4") is None


# ----------------------------------------------------------------------
# Findings: severity and confidence are settled centrally, not per call site.
# ----------------------------------------------------------------------
def test_confidence_matches_the_contract_table():
    assert qc.CONFIDENCE["black_frame"] == 0.95
    assert qc.CONFIDENCE["freeze_frame"] == 0.95
    assert qc.CONFIDENCE["loudness"] == 0.95
    assert qc.CONFIDENCE["clipping"] == 0.95
    assert qc.CONFIDENCE["duration"] == 0.95
    assert qc.CONFIDENCE["resolution"] == 0.95
    assert qc.CONFIDENCE["no_go_topic"] == 0.9
    assert qc.CONFIDENCE["silence_gap"] == 0.85
    assert qc.CONFIDENCE["captions_sync"] == 0.8
    assert qc.CONFIDENCE["speaker_count"] == 0.8
    assert qc.CONFIDENCE["duplicate_segment"] == 0.7
    assert qc.CONFIDENCE["mid_word_cut"] == 0.6
    assert qc.CONFIDENCE["segment_order"] == 0.5


def test_a_no_go_hit_is_always_an_error():
    row = qc.make_finding("run", "ep", "no_go_topic", "warning", "spoken at 12:00")
    assert row["severity"] == "error"
    assert row["confidence"] == 0.9


def test_a_no_go_row_that_says_we_could_not_look_stays_info():
    # "We did not look" must never render as a consent breach, and a breach must
    # never render as a note. Both halves of that matter.
    row = qc.make_finding("run", "ep", "no_go_topic", "info", "no transcript available")
    assert row["severity"] == "info"


def test_finding_carries_the_run_and_episode_and_rounded_timestamps():
    row = qc.make_finding("run-1", "ep-1", "silence_gap", "warning", "dead air",
                          754.2129, 755.0311)
    assert row["run_id"] == "run-1"
    assert row["studio_episode_id"] == "ep-1"
    assert row["timestamp_start"] == 754.21 and row["timestamp_end"] == 755.03
    assert row["severity"] == "warning"


def test_finding_omits_the_episode_id_when_there_is_none():
    row = qc.make_finding("run-1", None, "duration", "info", "x")
    assert "studio_episode_id" not in row
    assert "timestamp_start" not in row


def test_timecode_reads_like_a_scrub_position():
    assert qc.timecode(754.2) == "12:34"
    assert qc.timecode(3611) == "1:00:11"
    assert qc.timecode(None) == "?"


# ----------------------------------------------------------------------
# worker/app.py: validate_episode_qc_payload (Modal + fastapi stubbed out, the
# same _load_worker_app pattern as test_audiogram_moment_job.py).
# ----------------------------------------------------------------------
def _load_worker_app():
    for mod in ("modal", "fastapi"):
        sys.modules.setdefault(mod, MagicMock())
    spec = importlib.util.spec_from_file_location(
        "worker_app_under_test_qc", os.path.join(ROOT, "worker", "app.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRIVE_URL = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view"


def _payload(**over):
    p = {"action": "episode_qc",
         "job_id": "3e1c2f9a-0000-4000-8000-000000000000",
         "url": DRIVE_URL,
         "episode_id": "8b2d4e6f-1111-4000-8000-000000000000",
         "expected": {"duration_sec": 3612, "speakers": 2, "loudness_lufs": -16.0},
         "no_go_terms": ["a redacted phrase"],
         "transcript": {"words": [{"text": "Before", "start": 1.074, "end": 1.395,
                                   "speaker": 0}]},
         "utterances": [{"speaker": "A", "text": "Before", "start": 1.07, "end": 10.6}]}
    p.update(over)
    return p


def test_valid_episode_qc_payload_passes():
    app = _load_worker_app()
    assert app.validate_episode_qc_payload(_payload()) is None


def test_job_id_and_url_are_required():
    app = _load_worker_app()
    for field in ("job_id", "url"):
        p = _payload()
        del p[field]
        assert field in app.validate_episode_qc_payload(p)
        p[field] = "   "
        assert field in app.validate_episode_qc_payload(p)


def test_a_youtube_url_is_rejected_with_a_clear_reason():
    app = _load_worker_app()
    for url in ("https://www.youtube.com/watch?v=iTX6b2Z01II",
                "https://youtu.be/iTX6b2Z01II"):
        reason = app.validate_episode_qc_payload(_payload(url=url))
        # QC has to run on the file about to be published, not on one already
        # published and re-encoded by YouTube.
        assert reason and "Drive" in reason and "YouTube" in reason


def test_a_non_drive_url_is_rejected():
    app = _load_worker_app()
    reason = app.validate_episode_qc_payload(_payload(url="https://example.com/master.mp4"))
    assert reason and "Google Drive" in reason


def test_every_drive_link_shape_is_accepted():
    app = _load_worker_app()
    file_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    for url in (f"https://drive.google.com/file/d/{file_id}/view?usp=sharing",
                f"https://drive.google.com/open?id={file_id}",
                f"https://drive.google.com/uc?id={file_id}"):
        assert app.validate_episode_qc_payload(_payload(url=url)) is None


def test_optional_fields_must_still_be_the_right_shape():
    app = _load_worker_app()
    v = app.validate_episode_qc_payload
    assert v(_payload(expected="nope")) is not None
    assert v(_payload(no_go_terms="nope")) is not None
    assert v(_payload(no_go_terms=[1, 2])) is not None
    assert v(_payload(transcript="nope")) is not None
    assert v(_payload(utterances={"speaker": "A"})) is not None
    assert v(_payload(checks="black_frame")) is not None
    assert v(_payload(checks=[1])) is not None
    assert v(_payload(episode_id="  ")) is not None


def test_optional_fields_may_be_absent():
    app = _load_worker_app()
    p = _payload()
    for field in ("expected", "no_go_terms", "transcript", "utterances", "episode_id"):
        del p[field]
    assert app.validate_episode_qc_payload(p) is None


def test_a_checks_subset_is_accepted():
    app = _load_worker_app()
    assert app.validate_episode_qc_payload(
        _payload(checks=["black_frame", "silence_gap", "no_go_topic"])) is None


def test_non_dict_payload_is_rejected():
    app = _load_worker_app()
    assert app.validate_episode_qc_payload(None) is not None
    assert app.validate_episode_qc_payload("nope") is not None


def test_every_check_the_worker_advertises_has_a_confidence():
    app = _load_worker_app()
    # CHECK_TYPES is what the endpoint validates a `checks` subset against, so a
    # type without a confidence would reach the UI as a bare 0.5 default.
    assert app.QC_TRANSCRIPT_CHECKS <= qc.CHECK_TYPES
    assert qc.CHECK_TYPES == set(qc.CONFIDENCE)
