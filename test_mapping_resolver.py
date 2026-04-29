"""Offline tests for the mapping resolver pipeline."""

from mapping_resolver import resolve, find_workorders_in_text


def test_workorder_in_name_resolves_directly():
    """Stage 1: regex on tempo.account.name finds a workorder."""
    account = {
        "id": 999,
        "name": "ACME - Operations - Cloud (1018-12345-001)",
    }
    result = resolve(account, mapping={})
    assert result.arbauft == "1018-12345-001"
    assert result.source == "name"
    assert result.conflict_with is None


def test_no_name_match_falls_through_to_file():
    """Stage 2: no regex hit on name → look up mapping by tempo id."""
    account = {"id": 42, "name": "ACME - generic name without code"}
    mapping = {
        "42": {"unit4_arbauft": "1018-99999-100", "tempo_name": "ACME"},
    }
    result = resolve(account, mapping)
    assert result.arbauft == "1018-99999-100"
    assert result.source == "file"


def test_no_match_anywhere_returns_unresolved():
    """Stage 3: no name regex, no file entry → arbauft is None."""
    account = {"id": 7, "name": "Project X"}
    result = resolve(account, mapping={})
    assert result.arbauft is None
    assert result.source is None


def test_conflict_when_name_and_file_disagree():
    """Both name and file have a workorder, and they differ → conflict."""
    account = {
        "id": 42,
        "name": "ACME (1018-11111-100)",
    }
    mapping = {
        "42": {"unit4_arbauft": "1018-22222-200"},
    }
    result = resolve(account, mapping)
    assert result.arbauft is None
    assert result.source == "conflict"
    assert result.conflict_with == "1018-22222-200"
    assert "1018-11111-100" in result.name_matches


def test_name_and_file_agree_returns_file_source():
    """No conflict: same workorder in both. Source=name (regex was first)."""
    account = {
        "id": 42,
        "name": "ACME (1018-12345-001)",
    }
    mapping = {
        "42": {"unit4_arbauft": "1018-12345-001"},
    }
    result = resolve(account, mapping)
    assert result.arbauft == "1018-12345-001"
    # Either source is fine; caller doesn't need to distinguish when consistent
    assert result.source in ("name", "file")
    assert result.conflict_with is None


def test_multiple_workorders_in_name_is_ambiguous():
    """Name has two workorder codes — caller must disambiguate."""
    account = {
        "id": 42,
        "name": "Old: 1018-11111-100, new: 1018-22222-200",
    }
    result = resolve(account, mapping={})
    assert result.arbauft is None
    assert result.source == "conflict"
    assert result.name_matches == ("1018-11111-100", "1018-22222-200")


def test_find_workorders_in_text_helper():
    assert find_workorders_in_text("") == ()
    assert find_workorders_in_text("nothing here") == ()
    assert find_workorders_in_text("just 1018-12345-001 here") == ("1018-12345-001",)
    assert find_workorders_in_text("two: 1018-11111-100 1018-22222-200") == (
        "1018-11111-100",
        "1018-22222-200",
    )
