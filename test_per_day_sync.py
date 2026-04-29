"""Offline tests for per-day sync filtering and tracking-log writes."""

from pathlib import Path

import pytest

from models import TempoWorklog, Unit4Entry
from sync_tempo_to_unit4 import TrackingLog


def _wl(wid: int, date: str = "2026-04-27") -> TempoWorklog:
    return TempoWorklog(
        worklog_id=wid,
        issue_id=wid,
        issue_key=f"PROJ-{wid}",
        issue_summary="",
        date=date,
        hours=1.0,
        description="",
        account_key="EXAMPLE",
        account_name="Example",
        arbauft="1234-56789-001",
    )


def _entry(wid: int) -> Unit4Entry:
    return Unit4Entry(
        ticketno=f"PROJ-{wid}",
        arbauft="1234-56789-001",
        text=f"[WL:{wid}] sample",
        worklog_id=wid,
    )


def test_existing_entries_filter_keeps_only_target_day_ids():
    """The filter that runs after extract_entries() in day-auto mode."""
    valid_worklogs = [_wl(101, "2026-04-27"), _wl(102, "2026-04-27")]
    existing = [_entry(101), _entry(102), _entry(900), _entry(901)]

    target_wl_ids = {wl.worklog_id for wl in valid_worklogs}
    filtered = [e for e in existing if e.worklog_id in target_wl_ids]

    assert {e.worklog_id for e in filtered} == {101, 102}
    # Other days' markers stay untouched
    assert 900 not in {e.worklog_id for e in filtered}
    assert 901 not in {e.worklog_id for e in filtered}


def test_tracking_log_writes_block(tmp_path: Path):
    log = TrackingLog(path=str(tmp_path / "sync_history.log"))

    log.open_block(mode="day-auto", week="202618", day="2026-04-27")
    log.log_delete(101, "PROJ-1")
    log.log_create(_wl(101))
    log.log_create(_wl(102))
    log.close_block(save_status="ok")

    content = (tmp_path / "sync_history.log").read_text()
    assert "mode=day-auto" in content
    assert "week=202618" in content
    assert "day=2026-04-27" in content
    assert "DELETE [WL:101] PROJ-1" in content
    assert "CREATE [WL:101] PROJ-101 1.0h 1234-56789-001" in content
    assert "CREATE [WL:102] PROJ-102 1.0h 1234-56789-001" in content
    assert "SAVE ok" in content


def test_tracking_log_capture_ref(tmp_path: Path):
    log = TrackingLog(path=str(tmp_path / "sync_history.log"))
    log.open_block(mode="day-auto", week="202618", day="2026-04-27")
    log.close_block(save_status="fail", capture_ref="captures/RUN_test")

    content = (tmp_path / "sync_history.log").read_text()
    assert "SAVE fail ref=captures/RUN_test" in content


def test_tracking_log_appends_across_blocks(tmp_path: Path):
    log = TrackingLog(path=str(tmp_path / "sync_history.log"))
    log.open_block("day-auto", "202618", "2026-04-27")
    log.close_block("ok")
    log.open_block("day-auto", "202618", "2026-04-28")
    log.close_block("ok")

    content = (tmp_path / "sync_history.log").read_text()
    assert content.count("=== ") == 2
    assert content.count("SAVE ok") == 2


def test_tracking_log_write_failure_does_not_raise(tmp_path: Path):
    """A bad path must not abort the sync — log writes are self-protected."""
    bad_path = tmp_path / "no" / "such" / "dir" / "sync.log"
    log = TrackingLog(path=str(bad_path))
    # Must not raise
    log.open_block("day-auto", "202618", "2026-04-27")
    log.log_delete(101, "PROJ-1")
    log.log_create(_wl(101))
    log.close_block("ok")
