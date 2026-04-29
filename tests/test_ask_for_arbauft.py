"""Offline tests for the interactive ask_for_arbauft prompt.

Covers the two cases the user keeps reminding us about:

1. Resolver returned source="conflict" — both name and file have a
   workorder, but they disagree (or the name itself has multiple).
   The prompt must surface the conflict to the user.
2. Resolver returned source=None / arbauft=None — neither name nor file
   resolved. The prompt must show the help_urls (from config.json,
   never hardcoded in code) so the user has a place to look up the
   workorder.

Both cases must persist the user's answer into mapping.json on success.
"""

import builtins
from pathlib import Path

import pytest

import j2u4.utils as utils
from j2u4.cli import ask_for_arbauft
from j2u4.mapping_resolver import ResolveResult
from j2u4.models import TempoWorklog


def _wl_with_resolve_result(rr: ResolveResult, account_key: str = "42") -> TempoWorklog:
    wl = TempoWorklog(
        worklog_id=999,
        issue_id=1,
        issue_key="PROJ-1",
        issue_summary="hello",
        date="2026-04-29",
        hours=1.0,
        description="",
        account_key=account_key,
        account_name="ACME (1018-11111-100)",
        arbauft=None,
    )
    wl._resolve_result = rr  # type: ignore[attr-defined]
    return wl


@pytest.fixture
def isolated_mapping(tmp_path, monkeypatch):
    """Make load_mapping/save_mapping point at a tmp file so tests
    don't write into the user's real mapping.json."""
    mapping_file = tmp_path / "mapping.json"
    monkeypatch.setenv("J2U4_CONFIG_DIR", str(tmp_path))
    return mapping_file


def test_prompt_for_conflict_name_vs_file_shows_both(capsys, monkeypatch, isolated_mapping):
    """User must see what the name says AND what the file says."""
    rr = ResolveResult(
        arbauft=None,
        source="conflict",
        conflict_with="1018-22222-200",
        name_matches=("1018-11111-100",),
    )
    wl = _wl_with_resolve_result(rr)
    monkeypatch.setattr(builtins, "input", lambda: "1018-11111-100")

    arbauft = ask_for_arbauft(wl, mapping={}, help_urls=[])
    out = capsys.readouterr().out
    assert "CONFLICT" in out
    assert "1018-11111-100" in out  # what the name says
    assert "1018-22222-200" in out  # what the file said
    assert arbauft == "1018-11111-100"


def test_prompt_for_multiple_workorders_in_name(capsys, monkeypatch, isolated_mapping):
    """When the Tempo account name itself has multiple codes, all are listed."""
    rr = ResolveResult(
        arbauft=None,
        source="conflict",
        conflict_with=None,
        name_matches=("1018-11111-100", "1018-22222-200"),
    )
    wl = _wl_with_resolve_result(rr)
    monkeypatch.setattr(builtins, "input", lambda: "1018-22222-200")

    ask_for_arbauft(wl, mapping={}, help_urls=[])
    out = capsys.readouterr().out
    assert "CONFLICT" in out
    assert "2 workorder codes" in out
    assert "1018-11111-100" in out
    assert "1018-22222-200" in out


def test_prompt_no_match_shows_help_urls(capsys, monkeypatch, isolated_mapping):
    """When neither name nor file resolved, the prompt must surface the
    help_urls passed in (which come from config.mapping.help_urls in
    real runs, not from any hardcoded string in code)."""
    rr = ResolveResult(arbauft=None, source=None, conflict_with=None, name_matches=())
    wl = _wl_with_resolve_result(rr)
    help_urls = [
        "https://example.invalid/wiki/customer-projects",
        "https://example.invalid/wiki/internal-projects",
    ]
    monkeypatch.setattr(builtins, "input", lambda: "1018-99999-100")

    ask_for_arbauft(wl, mapping={}, help_urls=help_urls)
    out = capsys.readouterr().out
    assert "Look up the workorder in" in out
    for url in help_urls:
        assert url in out


def test_prompt_no_help_urls_does_not_print_links(capsys, monkeypatch, isolated_mapping):
    """If help_urls is empty/None, no 'Look up' section is shown — keeps
    the prompt minimal in setups that don't have wiki links."""
    rr = ResolveResult(arbauft=None, source=None, conflict_with=None, name_matches=())
    wl = _wl_with_resolve_result(rr)
    monkeypatch.setattr(builtins, "input", lambda: "1018-99999-100")

    ask_for_arbauft(wl, mapping={}, help_urls=[])
    out = capsys.readouterr().out
    assert "Look up the workorder in" not in out
    assert "https://" not in out


def test_prompt_skip_returns_none_and_does_not_persist(monkeypatch, isolated_mapping):
    rr = ResolveResult(arbauft=None, source=None, conflict_with=None, name_matches=())
    wl = _wl_with_resolve_result(rr)
    monkeypatch.setattr(builtins, "input", lambda: "SKIP")

    mapping: dict = {}
    arbauft = ask_for_arbauft(wl, mapping, help_urls=[])
    assert arbauft is None
    assert mapping == {}  # not persisted


def test_prompt_invalid_format_returns_none_and_does_not_persist(monkeypatch, isolated_mapping):
    rr = ResolveResult(arbauft=None, source=None, conflict_with=None, name_matches=())
    wl = _wl_with_resolve_result(rr)
    monkeypatch.setattr(builtins, "input", lambda: "not-an-arbauft")

    mapping: dict = {}
    arbauft = ask_for_arbauft(wl, mapping, help_urls=[])
    assert arbauft is None
    assert mapping == {}  # not persisted


def test_prompt_valid_input_persists_to_mapping(monkeypatch, isolated_mapping):
    rr = ResolveResult(arbauft=None, source=None, conflict_with=None, name_matches=())
    wl = _wl_with_resolve_result(rr, account_key="42")
    monkeypatch.setattr(builtins, "input", lambda: "1018-99999-100")

    mapping: dict = {}
    arbauft = ask_for_arbauft(wl, mapping, help_urls=[])
    assert arbauft == "1018-99999-100"
    assert mapping["42"]["unit4_arbauft"] == "1018-99999-100"
    # Also persisted to the on-disk mapping.json (J2U4_CONFIG_DIR is tmp).
    on_disk = isolated_mapping
    assert on_disk.exists()
    assert "1018-99999-100" in on_disk.read_text()
