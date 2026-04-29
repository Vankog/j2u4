"""Resolve a Tempo account to a Unit4 ArbAuft.

Three-stage pipeline (in order):

1. **Tempo account name regex** — if the account name contains a workorder
   pattern `1018-NNNNN-NNN`, that is the canonical source. Some teams pin
   the workorder directly in the Tempo account name; for those, no
   mapping file is needed.
2. **Local mapping.json** — fallback for accounts without an embedded
   workorder.
3. **Unresolved** — caller prompts the user (see `prompt_for_arbauft` in
   the sync script). The prompt persists into mapping.json on success.

If both stage 1 and stage 2 yield a value AND they disagree, the caller
gets a `ResolveResult` with `.conflict_with` set so it can prompt the
user to pick one. We do NOT silently prefer either source — that would
hide drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ARBAUFT_RE = re.compile(r"1018-\d{5}-\d{3}")


@dataclass
class ResolveResult:
    """Outcome of resolving one Tempo account.

    arbauft        — the resolved code, or None if unresolved
    source         — "name" / "file" / "conflict" / None
    conflict_with  — only set when source == "conflict"; the *other* code
                     so the caller can present both options to the user
    name_matches   — all workorder codes found in the account name (>1
                     means the name itself is ambiguous)
    """

    arbauft: str | None
    source: str | None
    conflict_with: str | None = None
    name_matches: tuple[str, ...] = ()


def find_workorders_in_text(text: str) -> tuple[str, ...]:
    """Return all 1018-NNNNN-NNN matches in the given text, in order."""
    if not text:
        return ()
    return tuple(m.group(0) for m in ARBAUFT_RE.finditer(text))


def resolve(account: dict, mapping: dict) -> ResolveResult:
    """Resolve a Tempo account dict to a ResolveResult.

    `account` must have at least 'id' and 'name'.
    `mapping` is the file-loaded dict, keyed by stringified account id.
    """
    name = account.get("name") or ""
    acc_id = str(account.get("id") or "")

    name_matches = find_workorders_in_text(name)
    file_arbauft = None
    if acc_id and acc_id in mapping:
        file_arbauft = mapping[acc_id].get("unit4_arbauft") or None

    # If the name has multiple workorders, that is its own kind of
    # ambiguity — surface so the caller can ask.
    if len(name_matches) > 1:
        return ResolveResult(
            arbauft=None,
            source="conflict",
            conflict_with=file_arbauft,
            name_matches=name_matches,
        )

    name_arbauft = name_matches[0] if name_matches else None

    if name_arbauft and file_arbauft and name_arbauft != file_arbauft:
        return ResolveResult(
            arbauft=None,
            source="conflict",
            conflict_with=file_arbauft,
            name_matches=name_matches,
        )

    if name_arbauft:
        return ResolveResult(arbauft=name_arbauft, source="name", name_matches=name_matches)
    if file_arbauft:
        return ResolveResult(arbauft=file_arbauft, source="file", name_matches=name_matches)
    return ResolveResult(arbauft=None, source=None, name_matches=name_matches)
