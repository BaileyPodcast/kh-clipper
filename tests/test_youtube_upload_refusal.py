"""
One upload per row (2026-09-07): kh-studio POSTed the same YouTube upload twice
and two private copies of the same master landed on the channel. kh-studio now
refuses the second click; the worker refuses too, so a row that already carries
a video_id never gets a second videos.insert.

    python -m pytest tests/test_youtube_upload_refusal.py
"""
import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)


def _load_worker_app():
    for mod in ("modal", "fastapi"):
        sys.modules.setdefault(mod, MagicMock())
    spec = importlib.util.spec_from_file_location(
        "worker_app_under_test_refusal", os.path.join(ROOT, "worker", "app.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


app = _load_worker_app()


def test_no_video_id_means_proceed():
    assert app.reupload_refusal(None) is None
    assert app.reupload_refusal({}) is None
    assert app.reupload_refusal({"video_id": None, "state": "queued"}) is None
    assert app.reupload_refusal({"video_id": "", "state": "failed"}) is None


def test_a_video_id_means_refuse_and_the_reason_names_it():
    reason = app.reupload_refusal({"video_id": "4ai7Sk6RZx4", "state": "queued"})
    assert reason is not None
    assert "4ai7Sk6RZx4" in reason
    assert "duplicate" in reason


def test_refuse_duplicate_upload_records_the_reason_and_leaves_video_id_alone(monkeypatch):
    patches = []
    monkeypatch.setattr(app, "get_upload_row", lambda _id: {"id": "u1", "video_id": "vid123", "state": "queued"})
    monkeypatch.setattr(app, "patch_upload", lambda _id, fields: patches.append(fields))

    assert app.refuse_duplicate_upload("u1") is True
    assert len(patches) == 1
    assert patches[0]["state"] == "failed"
    assert "vid123" in patches[0]["error"]
    assert "video_id" not in patches[0]


def test_refuse_duplicate_upload_lets_a_fresh_row_through_without_writing(monkeypatch):
    patches = []
    monkeypatch.setattr(app, "get_upload_row", lambda _id: {"id": "u1", "video_id": None, "state": "queued"})
    monkeypatch.setattr(app, "patch_upload", lambda _id, fields: patches.append(fields))

    assert app.refuse_duplicate_upload("u1") is False
    assert patches == []


def test_an_unreadable_row_is_not_a_refusal(monkeypatch):
    # A transient read failure must not strand a legitimate job.
    monkeypatch.setattr(app, "get_upload_row", lambda _id: None)
    monkeypatch.setattr(app, "patch_upload", lambda _id, fields: (_ for _ in ()).throw(AssertionError("wrote")))
    assert app.refuse_duplicate_upload("u1") is False
