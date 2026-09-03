"""
process_youtube_upload's master source (kh-studio db/300, 2026-09-03): the
master is a Google Drive link (gdown, unchanged) OR a signed Supabase Storage
URL for a file the producer uploaded from their own computer (streamed https
download). master_source_kind is the pure classifier both branches hang off;
_master_extension keeps the local filename safe. Modal + fastapi are stubbed so
worker/app.py imports without a deployment.

    python -m pytest tests/test_youtube_master_source.py
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
        "worker_app_under_test_master", os.path.join(ROOT, "worker", "app.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


app = _load_worker_app()

DRIVE = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view?usp=sharing"
SIGNED = ("https://nrmirzcjkdysxgfjgrnl.supabase.co/storage/v1/object/sign/studio-video/"
          "youtube-masters/ep1/abc.mp4?token=eyJhbGciOi")


def test_drive_link_is_drive_and_keeps_the_gdown_path():
    assert app.master_source_kind(DRIVE) == "drive"
    assert app.master_source_kind("https://drive.google.com/open?id=1AbCdEfGhIjKlMnOpQrStUvWxYz012345") == "drive"


def test_signed_storage_url_is_https():
    assert app.master_source_kind(SIGNED) == "https"


def test_anything_else_is_rejected_not_guessed():
    assert app.master_source_kind("") is None
    assert app.master_source_kind(None) is None
    assert app.master_source_kind("http://insecure.example/master.mp4") is None
    assert app.master_source_kind("ftp://x/y.mp4") is None


def test_master_extension_comes_from_the_name_and_can_never_form_a_path():
    assert app._master_extension("Final Master.MOV") == "mov"
    assert app._master_extension("episode.mkv") == "mkv"
    assert app._master_extension("noext") == "mp4"
    assert app._master_extension(None) == "mp4"
    assert app._master_extension("evil.../../x") == "mp4"
