"""Offline tests for the failure-capture helper.

Verifies _save_failure_chunk produces the expected folder layout and
context.json schema. Does not exercise the live browser or playwright;
the playwright tracing object is replaced by an AsyncMock.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from models import TempoWorklog
from unit4_browser import Unit4Browser


def _make_browser(tmp_path, capture_cap: int = 10):
    config = {
        "unit4": {"url": "http://localhost"},
        "debug": {
            "capture_enabled": True,
            "capture_dir": str(tmp_path),
            "capture_cap": capture_cap,
            "capture_video": False,
        },
    }
    b = Unit4Browser(config)
    b._capture_run_dir = tmp_path / "RUN_test"
    b._capture_run_dir.mkdir(parents=True, exist_ok=True)

    tracing = MagicMock()
    tracing.stop_chunk = AsyncMock()
    b._context = MagicMock()
    b._context.tracing = tracing
    b._tracing_active = True
    b._chunk_active = True

    b._page = MagicMock()
    b._page.url = "http://example/test"
    return b, tracing


def _sample_worklog(issue_key: str = "PROJ-123") -> TempoWorklog:
    return TempoWorklog(
        worklog_id=12345,
        issue_id=99,
        issue_key=issue_key,
        issue_summary="Test ticket",
        date="2026-04-28",
        hours=1.5,
        description="Implement failure capture",
        account_key="EXAMPLE-DEV",
        account_name="Example Customer - Dev",
        arbauft="1234-56789-001",
    )


def test_save_failure_chunk_writes_expected_files(tmp_path):
    b, tracing = _make_browser(tmp_path)
    wl = _sample_worklog()

    asyncio.run(b._save_failure_chunk("CREATE", wl, exc_text="Traceback (most recent...)"))

    folders = [p for p in b._capture_run_dir.iterdir() if p.is_dir()]
    assert len(folders) == 1
    folder = folders[0]
    assert "_CREATE_" in folder.name
    assert folder.name.endswith("_PROJ-123")

    # stop_chunk called with a path argument pointing into the folder
    tracing.stop_chunk.assert_awaited_once()
    kwargs = tracing.stop_chunk.await_args.kwargs
    assert "path" in kwargs
    assert kwargs["path"].endswith("trace.zip")
    assert str(folder) in kwargs["path"]

    ctx = json.loads((folder / "context.json").read_text())
    assert ctx["step"] == "CREATE"
    assert ctx["worklog"]["issue_key"] == "PROJ-123"
    assert ctx["worklog"]["arbauft"] == "1234-56789-001"
    assert ctx["worklog"]["hours"] == 1.5
    assert "Traceback" in (ctx["exception"] or "")
    assert "page_errors_recent" in ctx
    assert "failed_requests_recent" in ctx
    assert ctx["page_url"] == "http://example/test"

    readme = (folder / "README.txt").read_text()
    assert "playwright show-trace" in readme

    assert b._capture_count == 1
    assert b._chunk_active is False


def test_capture_cap_suppresses_save(tmp_path):
    b, tracing = _make_browser(tmp_path, capture_cap=2)
    b._capture_count = 2  # already at cap
    wl = _sample_worklog("X-1")

    asyncio.run(b._save_failure_chunk("CREATE", wl))

    folders = [p for p in b._capture_run_dir.iterdir() if p.is_dir()]
    assert folders == []
    # When cap is hit, stop_chunk is called WITHOUT a path (discard)
    tracing.stop_chunk.assert_awaited_once_with()
    assert b._capture_count == 2
    assert b._chunk_active is False


def test_save_no_op_if_no_chunk_active(tmp_path):
    b, tracing = _make_browser(tmp_path)
    b._chunk_active = False
    wl = _sample_worklog()

    asyncio.run(b._save_failure_chunk("CREATE", wl))

    folders = [p for p in b._capture_run_dir.iterdir() if p.is_dir()]
    assert folders == []
    tracing.stop_chunk.assert_not_called()
    assert b._capture_count == 0


def test_save_handles_missing_worklog(tmp_path):
    b, tracing = _make_browser(tmp_path)

    asyncio.run(b._save_failure_chunk("SET_WEEK", None, exc_text=None))

    folders = [p for p in b._capture_run_dir.iterdir() if p.is_dir()]
    assert len(folders) == 1
    folder = folders[0]
    assert folder.name.endswith("_NA")
    ctx = json.loads((folder / "context.json").read_text())
    assert ctx["worklog"] is None
    assert ctx["exception"] is None
    assert ctx["step"] == "SET_WEEK"
