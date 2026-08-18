"""
Loudness normalisation (src/loudness.py): command construction + the atomic,
non-fatal replace behaviour. subprocess is mocked throughout, no ffmpeg runs.

    python -m pytest tests/test_loudness.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import loudness  # noqa: E402


def test_build_command_targets_youtube_loudness():
    cmd = loudness.build_command("in.mp4", "out.mp4")
    assert cmd[0] == "ffmpeg" and "-y" in cmd
    assert cmd[cmd.index("-i") + 1] == "in.mp4"
    assert cmd[-1] == "out.mp4"
    # Video stream copied, never a second video encode after the pinned CRF 18.
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[cmd.index("-af") + 1] == "loudnorm=I=-14:TP=-1.0:LRA=11"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert cmd[cmd.index("-b:a") + 1] == "192k"
    assert "+faststart" in cmd[cmd.index("-movflags") + 1]


def test_normalize_replaces_file_on_success(tmp_path, monkeypatch):
    target = tmp_path / "clip.mp4"
    target.write_bytes(b"original")

    def fake_run(cmd, capture_output=True, text=True):
        # ffmpeg "writes" the temp output.
        Path(cmd[-1]).write_bytes(b"normalised")
        class R:
            returncode = 0
            stderr = ""
        return R()

    monkeypatch.setattr(loudness.subprocess, "run", fake_run)
    out = loudness.normalize(str(target))
    assert out == str(target)
    assert target.read_bytes() == b"normalised"
    assert not os.path.exists(str(target) + ".loudnorm.tmp.mp4")


def test_normalize_keeps_original_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "clip.mp4"
    target.write_bytes(b"original")

    def fake_run(cmd, capture_output=True, text=True):
        class R:
            returncode = 1
            stderr = "boom"
        return R()

    monkeypatch.setattr(loudness.subprocess, "run", fake_run)
    out = loudness.normalize(str(target))       # must not raise
    assert out == str(target)
    assert target.read_bytes() == b"original"
    assert not os.path.exists(str(target) + ".loudnorm.tmp.mp4")


def test_normalize_keeps_original_when_ffmpeg_writes_nothing(tmp_path, monkeypatch):
    target = tmp_path / "clip.mp4"
    target.write_bytes(b"original")

    def fake_run(cmd, capture_output=True, text=True):
        class R:
            returncode = 0
            stderr = ""
        return R()                              # "success" but no output file

    monkeypatch.setattr(loudness.subprocess, "run", fake_run)
    loudness.normalize(str(target))
    assert target.read_bytes() == b"original"
