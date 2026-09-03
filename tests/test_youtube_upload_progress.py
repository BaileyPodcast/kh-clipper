"""
The YouTube upload progress bar (Tony, 2026-09-04): the resumable PUT used to sit
at 15% for the whole transfer. _ProgressReader wraps the master file so bytes
leaving for YouTube patch the row at 5% steps, while still answering len() so
requests keeps Content-Length (YouTube rejects chunked encoding on this PUT).

    python -m pytest tests/test_youtube_upload_progress.py
"""
import importlib.util
import io
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
        "worker_app_under_test_progress", os.path.join(ROOT, "worker", "app.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


app = _load_worker_app()


def _drain(reader, block):
    while True:
        chunk = reader.read(block)
        if not chunk:
            break


def test_len_is_the_file_size_so_requests_sends_content_length():
    r = app._ProgressReader(io.BytesIO(b"x" * 1000), 1000, lambda d, t: None)
    assert len(r) == 1000


def test_reads_pass_every_byte_through_unchanged():
    data = bytes(range(256)) * 40
    out = io.BytesIO()
    r = app._ProgressReader(io.BytesIO(data), len(data), lambda d, t: None)
    while True:
        chunk = r.read(777)
        if not chunk:
            break
        out.write(chunk)
    assert out.getvalue() == data


def test_progress_fires_once_per_five_percent_step_and_reaches_100():
    seen = []
    r = app._ProgressReader(io.BytesIO(b"x" * 10_000), 10_000, lambda d, t: seen.append(d * 100 // t))
    _drain(r, 100)  # 100 reads of 1% each
    assert seen[-1] == 100
    assert len(seen) == 20                     # 5, 10, ... 100 (one per bucket)
    assert seen == sorted(seen)
    assert all(p % 5 == 0 for p in seen)


def test_a_failing_callback_never_breaks_the_upload():
    def boom(d, t):
        raise RuntimeError("supabase down")
    r = app._ProgressReader(io.BytesIO(b"x" * 500), 500, boom)
    _drain(r, 50)  # would raise on the first bucket if not swallowed


def test_upload_band_maps_0_to_100_onto_15_to_80():
    # The same arithmetic process_youtube_upload's _on_sent uses.
    band = lambda pct: 15 + int(pct * 0.65)  # noqa: E731
    assert band(0) == 15
    assert band(50) == 47
    assert band(100) == 80
