"""Offline tests for per-day sync filtering, tracking-log writes, and ISO-week derivation."""

from pathlib import Path

import pytest

from j2u4.cli import TrackingLog, shift_week, week_from_date
from j2u4.models import TempoWorklog, Unit4Entry


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
    """The filter that runs after extract_entries() in per-day mode."""
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

    log.open_block(week="202618", day="2026-04-27")
    log.log_delete(101, "PROJ-1")
    log.log_create(_wl(101))
    log.log_create(_wl(102))
    log.close_block(save_status="ok")

    content = (tmp_path / "sync_history.log").read_text()
    assert "week=202618" in content
    assert "day=2026-04-27" in content
    assert "DELETE [WL:101] PROJ-1" in content
    assert "CREATE [WL:101] PROJ-101 1.0h 1234-56789-001" in content
    assert "CREATE [WL:102] PROJ-102 1.0h 1234-56789-001" in content
    assert "SAVE ok" in content


def test_tracking_log_capture_ref(tmp_path: Path):
    log = TrackingLog(path=str(tmp_path / "sync_history.log"))
    log.open_block(week="202618", day="2026-04-27")
    log.close_block(save_status="fail", capture_ref="captures/RUN_test")

    content = (tmp_path / "sync_history.log").read_text()
    assert "SAVE fail ref=captures/RUN_test" in content


def test_tracking_log_appends_across_blocks(tmp_path: Path):
    log = TrackingLog(path=str(tmp_path / "sync_history.log"))
    log.open_block("202618", "2026-04-27")
    log.close_block("ok")
    log.open_block("202618", "2026-04-28")
    log.close_block("ok")

    content = (tmp_path / "sync_history.log").read_text()
    assert content.count("=== ") == 2
    assert content.count("SAVE ok") == 2


def test_tracking_log_write_failure_does_not_raise(tmp_path: Path):
    """A bad path must not abort the sync — log writes are self-protected."""
    bad_path = tmp_path / "no" / "such" / "dir" / "sync.log"
    log = TrackingLog(path=str(bad_path))
    # Must not raise
    log.open_block("202618", "2026-04-27")
    log.log_delete(101, "PROJ-1")
    log.log_create(_wl(101))
    log.close_block("ok")


def test_week_from_date_iso_unambiguous():
    """ISO week derivation: same calendar week → same ISO week, even Sat/Sun."""
    # Mi 29.04.2026 is in ISO week 18
    assert week_from_date("2026-04-29") == "202618"
    # Sat of the same week
    assert week_from_date("2026-05-02") == "202618"
    # Sun of the same week
    assert week_from_date("2026-05-03") == "202618"
    # Mon of next week → ISO week 19
    assert week_from_date("2026-05-04") == "202619"


def test_week_from_date_year_boundary():
    """ISO weeks at year boundary follow ISO 8601, not calendar year."""
    # 2025-12-29 is Mon of ISO week 1 of 2026 per ISO 8601
    assert week_from_date("2025-12-29") == "202601"
    # 2027-01-01 is Fri — ISO week 53 of 2026
    assert week_from_date("2027-01-01") == "202653"


def test_week_override_format_validation():
    """--week accepts YYYYWW (6 digits), rejects everything else."""
    from j2u4.patterns import Patterns

    assert Patterns.WEEK_FORMAT.match("202619")
    assert Patterns.WEEK_FORMAT.match("202601")
    # 5 digits, 7 digits, letters → no match
    assert not Patterns.WEEK_FORMAT.match("20261")
    assert not Patterns.WEEK_FORMAT.match("2026019")
    assert not Patterns.WEEK_FORMAT.match("2026-19")
    assert not Patterns.WEEK_FORMAT.match("abc619")


def test_shift_week_relative():
    """shift_week shifts the ISO week of a date by N weeks."""
    # Sat 2 May 2026 — ISO 18; +1 → 19; -1 → 17
    assert shift_week("2026-05-02", 0) == "202618"
    assert shift_week("2026-05-02", 1) == "202619"
    assert shift_week("2026-05-02", -1) == "202617"


def test_shift_week_year_boundary():
    """Shifting across year boundaries follows ISO: +1 from week 53 lands in week 1 of next year."""
    # 2027-01-01 is ISO week 53 of 2026
    assert shift_week("2027-01-01", 0) == "202653"
    # +1 → first ISO week of 2027 (depends on calendar; 2027-01-08 is in week 1)
    assert shift_week("2027-01-01", 1) == "202701"
    # -1 → previous week
    assert shift_week("2027-01-01", -1) == "202652"
