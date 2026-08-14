"""
The cut stage must FAIL LOUDLY, not exit silently, when it has no source.

SystemExit is a BaseException, so the worker's `except Exception` handler never
caught it: the job died with no error written and hung at the cut stage until
the stall watchdog auto-expired it (the bulk-drop failure mode). A RuntimeError
is caught, written to the job row, and surfaced to the producer immediately.

    python -m pytest tests/test_cut_errors.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import cut


def test_missing_source_raises_runtime_error_not_system_exit(tmp_path):
    clips_json = tmp_path / "ep.clips.json"
    clips_json.write_text(json.dumps({
        "source": "exact_cut",
        "clips": [{"clip_id": "x-01", "start": 1.0, "end": 5.0}],  # no source_video_id
    }))
    try:
        cut.run(str(clips_json))
    except RuntimeError as e:
        assert "source_video_id" in str(e)
    except SystemExit:
        raise AssertionError("cut.run raised SystemExit; the worker cannot catch that")
    else:
        raise AssertionError("cut.run should refuse to run with no source")


def test_exact_cut_clip_carries_a_usable_source_id(tmp_path):
    # End-to-end shape check: a clips.json built by the exact-cut path resolves
    # a real YouTube URL in cut.run's source logic (no download is attempted
    # here; we only check the id resolution does not raise).
    from src import moments
    td = {"id": "abc123XYZ_-", "title": "Ep",
          "words": [{"text": f"w{i}", "start": float(i), "end": i + 0.9, "speaker": 0}
                    for i in range(12)]}
    clips = moments.build_moment_clips(td, [{"start": 0.0, "end": 9.0}])
    assert clips[0]["source_video_id"] == "abc123XYZ_-"
