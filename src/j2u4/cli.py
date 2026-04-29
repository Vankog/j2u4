"""Sync Tempo worklogs to Unit4 (one day per invocation).

Usage (after `uv tool install --from . j2u4`):

    j2u4 --check                                # connectivity test
    j2u4                                        # dry-run for today
    j2u4 --day 2026-04-29                       # dry-run for that day
    j2u4 --day 2026-04-29 --execute             # sync that day
    j2u4 --day 2026-04-29 --execute --no-video  # skip browser video
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from getpass import getpass
from pathlib import Path

import requests

from j2u4.browser import Unit4Browser
from j2u4.clients import ApiError, JiraClient, TempoClient
from j2u4.models import TempoWorklog, Unit4Entry
from j2u4.patterns import Patterns
from j2u4.utils import (
    config_path,
    get_week_dates,
    load_config_safe,
    load_mapping,
    save_mapping,
    user_config_dir,
)


# ---------------------------------------------------------------------------
# Interactive `--init` setup (budjira-style: prompts + helper links)
# ---------------------------------------------------------------------------


def _ask(label: str, default: str | None = None, validate=None) -> str:
    """Prompt for a value, accept default with empty input, repeat on invalid."""
    while True:
        prompt = label
        if default:
            prompt += f" [{default}]"
        prompt += ": "
        value = input(prompt).strip() or (default or "")
        if not value:
            print("    (required, try again)")
            continue
        if validate is not None:
            err = validate(value)
            if err:
                print(f"    {err}")
                continue
        return value


def _ask_secret(label: str, existing: str | None = None) -> str:
    """Prompt for a token; input is hidden. If a value already exists,
    pressing ENTER keeps it."""
    suffix = " [keep existing]" if existing else ""
    while True:
        value = getpass(f"{label}{suffix}: ").strip()
        if not value and existing:
            return existing
        if not value:
            print("    (required, try again)")
            continue
        return value


def _validate_url(value: str) -> str | None:
    if not value.startswith(("http://", "https://")):
        return "URL must start with http:// or https://"
    return None


def interactive_init() -> int:
    """Walk the user through creating ~/.config/j2u4/config.json (or wherever
    j2u4 looks for its config). Loads existing values as defaults; tokens
    are prompted with hidden input and pressing ENTER keeps the current
    value."""
    target = config_path()
    existing: dict = {}
    if target.exists():
        try:
            existing = json.loads(target.read_text())
            print(f"[*] Found existing {target} — current values are offered as defaults.")
        except json.JSONDecodeError:
            print(f"[!] Existing {target} is not valid JSON; ignoring it.")

    print()
    print("=" * 60)
    print("j2u4 — interactive configuration")
    print("=" * 60)
    print()
    print("Three required sections (Jira / Tempo / Unit4) plus optional")
    print("Confluence help URLs for the mapping prompt. Press Ctrl-C to abort.")
    print()

    # ---- Jira ----
    print("[1/4] Jira (Atlassian Cloud)")
    print("      Create an API token at:")
    print("        https://id.atlassian.com/manage-profile/security/api-tokens")
    print()
    jira = existing.get("jira") or {}
    jira_url = _ask(
        "  Jira base URL (e.g. https://acme.atlassian.net)",
        jira.get("base_url"),
        _validate_url,
    )
    jira_email = _ask("  Your Atlassian email", jira.get("user_email"))
    jira_token = _ask_secret("  Jira API token (input hidden)", jira.get("api_token"))

    # ---- Tempo ----
    print()
    print("[2/4] Tempo Timesheets")
    print("      Create a token at:")
    print(f"        {jira_url.rstrip('/')}/plugins/servlet/ac/io.tempo.jira/tempo-app#!/configuration/api-integration")
    print()
    tempo_token = _ask_secret(
        "  Tempo API token (input hidden)", (existing.get("tempo") or {}).get("api_token")
    )

    # ---- Unit4 ----
    print()
    print("[3/4] Unit4 ERP")
    print("      URL pattern: https://ubw.unit4cloud.com/<TENANT>/Default.aspx")
    print()
    unit4_url = _ask(
        "  Unit4 URL", (existing.get("unit4") or {}).get("url"), _validate_url
    )

    # ---- Mapping help URLs (optional) ----
    print()
    print("[4/4] Mapping help URLs (optional)")
    print("      Shown in the prompt when an account can't be auto-resolved.")
    print("      Typically your team's Confluence pages with the workorder catalogue.")
    print("      Enter one URL per line, empty line to finish.")
    print()
    help_urls: list[str] = list((existing.get("mapping") or {}).get("help_urls") or [])
    if help_urls:
        print("      Existing URLs (will be kept; add more or hit ENTER to skip):")
        for u in help_urls:
            print(f"        - {u}")
    while True:
        u = input("    URL (empty to finish): ").strip()
        if not u:
            break
        if _validate_url(u):
            print(f"    {_validate_url(u)}")
            continue
        help_urls.append(u)
        print(f"      Added. ({len(help_urls)} total)")

    # ---- Build + show summary ----
    config: dict = {
        "jira": {"base_url": jira_url, "user_email": jira_email, "api_token": jira_token},
        "tempo": {"api_token": tempo_token},
        "unit4": {"url": unit4_url},
    }
    if help_urls:
        config["mapping"] = {"help_urls": help_urls}
    # Preserve any existing debug block — not part of the interactive flow
    if existing.get("debug"):
        config["debug"] = existing["debug"]

    print()
    print("=" * 60)
    print("Summary (tokens masked):")
    print("=" * 60)
    masked = json.loads(json.dumps(config))  # deep copy
    masked["jira"]["api_token"] = "*" * 8
    masked["tempo"]["api_token"] = "*" * 8
    print(json.dumps(masked, indent=2, ensure_ascii=False))
    print()
    confirm = input(f"Write to {target}? [y/N]: ").strip().lower()
    if confirm not in ("y", "yes", "j", "ja"):
        print("[*] Aborted, nothing written.")
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    print(f"[*] Wrote {target}")
    print()
    print("Verify with: j2u4 --check")
    return 0


class TrackingLog:
    """Append-only history log for sync runs.

    One block per sync invocation: header with timestamp + mode + week + day,
    one line per DELETE/CREATE action, closing SAVE line. Self-protected —
    a log-write failure must never abort the sync itself.
    """

    def __init__(self, path: str = "./sync_history.log"):
        self.path = Path(path)

    def _append(self, line: str) -> None:
        try:
            with self.path.open("a") as f:
                f.write(line)
        except Exception as e:
            print(f"[!] tracking log write failed: {e}")

    def open_block(self, week: str, day: str) -> None:
        ts = datetime.now().isoformat(timespec="seconds")
        self._append(f"\n=== {ts} week={week} day={day} ===\n")

    def log_delete(self, wl_id: int | None, ticket: str) -> None:
        if wl_id is None:
            return
        self._append(f"DELETE [WL:{wl_id}] {ticket}\n")

    def log_create(self, wl: TempoWorklog) -> None:
        self._append(
            f"CREATE [WL:{wl.worklog_id}] {wl.issue_key} {wl.hours}h {wl.arbauft}\n"
        )

    def close_block(self, save_status: str, capture_ref: str | None = None) -> None:
        suffix = f" ref={capture_ref}" if capture_ref else ""
        self._append(f"SAVE {save_status}{suffix}\n")


def check_connectivity(config: dict) -> bool:
    """Check connectivity to all required services.

    Returns:
        True if all checks pass, False otherwise.
    """
    print()
    print("=" * 50)
    print("CONNECTIVITY CHECK")
    print("=" * 50)
    print()

    all_ok = True
    warnings = []

    # Check Jira
    print("[1] Jira API...", end=" ", flush=True)
    try:
        jira = JiraClient(config)
        account_id = jira.get_my_account_id()
        print(f"OK (account: {account_id[:8]}...)")
    except ApiError as e:
        print(f"FAILED")
        print(f"    {e}")
        all_ok = False
    except Exception as e:
        print(f"FAILED")
        print(f"    Unexpected error: {e}")
        all_ok = False

    # Check Tempo
    print("[2] Tempo API...", end=" ", flush=True)
    try:
        tempo = TempoClient(config)
        # Just try to fetch worklogs for today to test auth
        jira = JiraClient(config)
        account_id = jira.get_my_account_id()
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        tempo.fetch_worklogs(account_id, today, today)
        print("OK")
    except ApiError as e:
        print(f"FAILED")
        print(f"    {e}")
        all_ok = False
    except Exception as e:
        print(f"FAILED")
        print(f"    Unexpected error: {e}")
        all_ok = False

    # Check Unit4 URL
    print("[3] Unit4 URL...", end=" ", flush=True)
    unit4_url = config.get("unit4", {}).get("url")
    if not unit4_url:
        print("FAILED")
        print("    unit4.url not configured in config.json")
        all_ok = False
    else:
        try:
            r = requests.head(unit4_url, timeout=10, allow_redirects=True)
            if r.ok or r.status_code in [302, 401, 403]:
                # 302/401/403 are OK - means the server is reachable
                print(f"OK ({unit4_url})")
            else:
                print(f"WARNING (HTTP {r.status_code})")
                print(f"    URL may be incorrect or server unavailable")
        except requests.exceptions.ConnectionError:
            print("FAILED")
            print(f"    Cannot connect to {unit4_url}")
            print("    Check if you're connected to VPN!")
            all_ok = False
        except requests.exceptions.Timeout:
            print("FAILED")
            print(f"    Connection timed out")
            all_ok = False

    # Check mapping file
    print("[4] Mapping file...", end=" ", flush=True)
    mapping = load_mapping()
    if not mapping:
        print("EMPTY (no mappings yet)")
        warnings.append("mapping")
    else:
        print(f"OK ({len(mapping)} account mappings)")

    print()
    print("=" * 50)
    if all_ok:
        print("All connectivity checks passed!")
    else:
        print("Some checks FAILED. Fix the issues above before syncing.")

    if "mapping" in warnings:
        print()
        print("NOTE: mapping.json is empty or missing.")
        print("      That is fine — the resolver will pick up workorders")
        print("      from Tempo account names automatically. For accounts")
        print("      without an embedded workorder, the sync will prompt")
        print("      and persist your answer.")
    print("=" * 50)
    print()

    return all_ok


def fetch_and_resolve_worklogs(
    config: dict, target_day: str, week_dates: tuple[str, str], mapping: dict
) -> tuple[list[TempoWorklog], list[TempoWorklog], set[int]]:
    """Fetch worklogs per Tempo account; resolve ArbAuft via the
    mapping_resolver pipeline; collect issue summaries best-effort.

    The whole-week worklog ids are returned alongside so the caller can
    detect orphans (Unit4 markers whose Tempo worklog no longer exists).

    Returns:
        - valid_worklogs: target-day worklogs with resolved ArbAuft
        - unmapped_worklogs: target-day worklogs that could not be resolved
        - week_worklog_ids: ALL worklog ids found across the whole week
          (used for orphan detection)
    """
    from j2u4.mapping_resolver import resolve as resolve_arbauft

    tempo = TempoClient(config)
    jira = JiraClient(config)

    # Per-account worklog endpoints return worklogs from EVERY user that
    # booked on that Tempo account — not just ours. Filter client-side
    # against the current user's Jira accountId so we don't accidentally
    # pull (or, worse, sync into Unit4) a colleague's worklogs.
    my_account_id = jira.get_my_account_id()

    accounts = tempo.fetch_accounts()
    open_accounts = [a for a in accounts if a.get("status") == "OPEN"]
    print(f"[*] {len(open_accounts)} open Tempo accounts to scan (filtering to current user)")

    week_from, week_to = week_dates
    valid_worklogs: list[TempoWorklog] = []
    unmapped_worklogs: list[TempoWorklog] = []
    week_worklog_ids: set[int] = set()
    issue_cache: dict[int, dict] = {}  # issue_id -> {"key", "summary"}
    pending_resolution: list[tuple[dict, dict]] = []  # (account, raw_worklog) target-day only
    mapping_dirty = [False]  # 1-element list so the inner loop can mutate it

    # Phase 1: collect everything across the whole week, per account
    for acc in open_accounts:
        key = acc.get("key")
        if not key:
            continue
        try:
            wls = tempo.fetch_worklogs_by_account(key, week_from, week_to)
        except Exception as e:
            print(f"[!] Tempo: skipping account {key}: {e}")
            continue
        for wl in wls:
            author_id = (wl.get("author") or {}).get("accountId")
            if author_id != my_account_id:
                continue  # not our worklog — skip silently
            week_worklog_ids.add(wl["tempoWorklogId"])
            if wl["startDate"] == target_day:
                pending_resolution.append((acc, wl))

    if not pending_resolution:
        return [], [], week_worklog_ids

    # Phase 2: resolve target-day entries
    for acc, wl in pending_resolution:
        worklog_id = wl["tempoWorklogId"]
        issue_id = wl.get("issue", {}).get("id")
        date = wl["startDate"]
        hours = wl["timeSpentSeconds"] / 3600
        description = wl.get("description", "")

        # Issue lookup best-effort (Jira; may 404 silently for tickets the
        # user has no permission on). Cache key + summary so repeat
        # worklogs on the same issue cost one Jira call total.
        if issue_id and issue_id not in issue_cache:
            issue_data = jira.get_issue_details(issue_id)
            if issue_data:
                fields = issue_data.get("fields", {})
                issue_cache[issue_id] = {
                    "key": issue_data.get("key") or "",
                    "summary": (fields.get("summary") or "")[:100],
                }
            else:
                issue_cache[issue_id] = {"key": "", "summary": ""}
        info = issue_cache.get(issue_id) or {"key": "", "summary": ""}
        issue_key = info["key"] or wl.get("issue", {}).get("key") or f"ID:{issue_id}"
        summary = info["summary"]

        result = resolve_arbauft(acc, mapping)

        # Auto-persist: when the Tempo account name itself carries the
        # workorder AND the file has no entry yet, write it back. This:
        #   - grows mapping.json silently as new accounts are seen
        #   - lets future runs detect drift via the conflict path if the
        #     Tempo name later changes
        # We never overwrite an existing file entry — drift resolution
        # stays explicit (user prompt).
        acc_id_str = str(acc.get("id") or "")
        if (
            result.source == "name"
            and result.arbauft
            and acc_id_str
            and acc_id_str not in mapping
        ):
            mapping[acc_id_str] = {
                "unit4_arbauft": result.arbauft,
                "tempo_name": acc.get("name") or "",
                "auto_synced_from_tempo": True,
            }
            mapping_dirty[0] = True

        worklog = TempoWorklog(
            worklog_id=worklog_id,
            issue_id=issue_id,
            issue_key=issue_key,
            issue_summary=summary,
            date=date,
            hours=hours,
            description=description,
            account_key=acc_id_str,
            account_name=acc.get("name") or "",
            arbauft=result.arbauft,
        )
        # Stash the resolve result for callers that want to handle conflicts
        worklog._resolve_result = result  # type: ignore[attr-defined]

        if result.arbauft:
            valid_worklogs.append(worklog)
        else:
            unmapped_worklogs.append(worklog)

    if mapping_dirty[0]:
        save_mapping(mapping)
        print(f"[*] Mapping file updated with {sum(1 for v in mapping.values() if v.get('auto_synced_from_tempo')):d} auto-synced entries (from tempo account names)")

    return valid_worklogs, unmapped_worklogs, week_worklog_ids


def ask_for_arbauft(
    worklog: TempoWorklog, mapping: dict, help_urls: list[str] | None = None
) -> str | None:
    """Interactively ask user for ArbAuft for an unmapped worklog.

    Surfaces the resolver's diagnostic info (was it conflict, multiple
    workorders in name, etc.) plus optional Confluence help URLs from
    config.json so the user has a concrete place to look up the code.
    """
    result = getattr(worklog, "_resolve_result", None)

    print()
    print(f"  Unmapped Tempo account: {worklog.account_key} ({worklog.account_name})")
    print(f"    Ticket : {worklog.issue_key}")
    if worklog.issue_summary:
        print(f"    Summary: {worklog.issue_summary[:60]}")

    if result is not None and result.source == "conflict":
        if len(result.name_matches) > 1:
            print(f"  [!] CONFLICT: account name contains {len(result.name_matches)} workorder codes:")
            for m in result.name_matches:
                print(f"      - {m}")
        elif result.name_matches and result.conflict_with:
            print(f"  [!] CONFLICT: name says {result.name_matches[0]}, mapping says {result.conflict_with}")
        print(f"      Pick one of the codes above, or enter a different one.")

    if help_urls:
        print(f"  Look up the workorder in:")
        for url in help_urls:
            print(f"      - {url}")

    print()
    print("  Enter ArbAuft (e.g., 1234-56789-001) or SKIP to skip: ", end="")

    arbauft = input().strip()

    if arbauft.upper() == "SKIP" or not arbauft:
        return None

    # Validate format
    if not Patterns.ARBAUFT.match(arbauft):
        print(f"  [!] Invalid format '{arbauft}', expected: XXXX-XXXXX-XXX")
        return None

    # Save to mapping
    mapping[worklog.account_key] = {
        "unit4_arbauft": arbauft,
        "tempo_name": worklog.account_name or "?",
        "sample_ticket": worklog.issue_key,
    }
    save_mapping(mapping)
    print(f"  [+] Saved mapping: {worklog.account_key} -> {arbauft}")

    return arbauft


async def sync(
    week: str,
    target_day: str,
    execute: bool,
    config_override: dict | None = None,
    slow_factor: int = 1,
):
    """Single-day Tempo→Unit4 sync. ISO week is derived from target_day."""
    dry_run = not execute
    mode_label = "EXECUTE" if execute else "DRY-RUN"

    print()
    print("=" * 70)
    print(f"SYNC TEMPO -> UNIT4 | Day {target_day} (week {week}) | Mode: {mode_label}")
    if slow_factor > 1:
        print(f"Slowness: {slow_factor}x (Playwright slow_mo and click timeouts scaled)")
    print("=" * 70)
    print()

    # Load config (or use the override passed by main(), which carries CLI
    # overrides for capture flags)
    config = config_override if config_override is not None else load_config_safe()
    if config is None:
        return

    mapping = load_mapping()
    unit4_url = config.get("unit4", {}).get("url")
    if not unit4_url:
        print("[!] Error: unit4.url not configured in config.json")
        return
    print(f"[*] Loaded mapping with {len(mapping)} accounts")

    # Fetch Tempo worklogs per account (Mo–So of the ISO week, then filter
    # to target_day). Per-account fetching is permission-friendly: no
    # Jira-issue read needed for the account resolution.
    print()
    print(f"[1] Fetching Tempo worklogs (whole week, per Tempo account)...")

    week_from, week_to = get_week_dates(week)
    try:
        valid_worklogs, unmapped_worklogs, week_worklog_ids = fetch_and_resolve_worklogs(
            config, target_day, (week_from, week_to), mapping
        )
    except ApiError as e:
        print()
        print(f"[!] API Error: {e}")
        print()
        print("    Run 'python sync_tempo_to_unit4.py --check' to diagnose the issue.")
        return

    print(f"    Target day {target_day}: valid={len(valid_worklogs)}, unmapped={len(unmapped_worklogs)}")
    print(f"    Whole week: {len(week_worklog_ids)} worklog ids (used for orphan detection)")

    # Handle unmapped worklogs interactively
    skipped_count = 0
    help_urls = (config.get("mapping") or {}).get("help_urls") or []
    if unmapped_worklogs:
        print()
        print("[!] Found unmapped worklogs. Enter ArbAuft or SKIP:")
        for wl in unmapped_worklogs:
            arbauft = ask_for_arbauft(wl, mapping, help_urls=help_urls)
            if arbauft:
                wl.arbauft = arbauft
                # Mark this worklog so the summary shows "from prompt"
                # instead of an empty source.
                if hasattr(wl, "_resolve_result"):
                    wl._resolve_result.source = "manual"
                valid_worklogs.append(wl)
            else:
                skipped_count += 1

    # Show summary
    print()
    print("[3] Summary of worklogs to sync:")
    total_hours = 0
    source_counts: dict[str, int] = {}
    for wl in sorted(valid_worklogs, key=lambda x: (x.date, x.issue_key)):
        result = getattr(wl, "_resolve_result", None)
        # Display label for the resolution source. "name" hits the
        # tempo-account-name regex, "file" hits mapping.json, "manual"
        # came in via the prompt during this run.
        src = (result.source if result is not None else None) or "manual"
        src_label = {"name": "tempo-name", "file": "file", "manual": "prompt"}.get(src, src)
        source_counts[src_label] = source_counts.get(src_label, 0) + 1
        print(
            f"    {wl.date} | {wl.hours:5.2f}h | {wl.issue_key:<15} | "
            f"{wl.arbauft} [{src_label}] [WL:{wl.worklog_id}]"
        )
        total_hours += wl.hours
    print(f"    {'─' * 60}")
    print(f"    Total: {total_hours:.2f}h across {len(valid_worklogs)} entries")
    if source_counts:
        breakdown = ", ".join(f"{n} from {src}" for src, n in sorted(source_counts.items()))
        print(f"    Resolved: {breakdown}")

    if not valid_worklogs:
        print()
        print("[*] No worklogs to sync. Done.")
        return

    tracking = TrackingLog()

    # Connect to Unit4
    print()
    print("[4] Connecting to Unit4...")

    async with Unit4Browser(config, slow_factor=slow_factor) as unit4:
        frame = await unit4.navigate_to_zeiterfassung()

        # Set week
        if not await unit4.set_week(week):
            print("[!] Failed to set week - page may not have loaded correctly")
            print("    Waiting for page to stabilize...")
            await asyncio.sleep(5)
            await unit4.set_week(week)

        # Wait for page to be ready (also checks if week is locked)
        if not await unit4.wait_for_ready():
            print()
            print("[!] Cannot sync - week is not editable.")
            print("    The week may have already been submitted (Bereit/Transferiert).")
            print()
            print("[*] Press ENTER to close browser...")
            try:
                await asyncio.get_event_loop().run_in_executor(None, input)
            except EOFError:
                pass
            return

        # Wait for table to load
        print()
        print("[5] Scanning existing entries for [WL:...] markers...")
        print("    Waiting for table to load...", end=" ", flush=True)
        await asyncio.sleep(3)

        # Deselect any selected row
        await unit4.page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
        print("OK")

        existing_entries = await unit4.extract_entries(debug=True)
        print(f"    Found {len(existing_entries)} synced entries (whole week)")

        # Combine two delete-sets:
        # 1. Target-day markers: their worklog_id is in today's valid_worklogs
        #    → delete + recreate (the day-sync core)
        # 2. Orphans: marker present in Unit4 but no longer in Tempo's whole
        #    week → delete (no recreate). Catches deleted Tempo worklogs.
        target_wl_ids = {wl.worklog_id for wl in valid_worklogs}
        orphan_ids = {
            e.worklog_id for e in existing_entries
            if e.worklog_id is not None and e.worklog_id not in week_worklog_ids
        }
        if orphan_ids:
            print(f"    [orphan-cleanup] {len(orphan_ids)} markers in Unit4 with no matching Tempo worklog — will be deleted:")
            for entry in existing_entries:
                if entry.worklog_id in orphan_ids:
                    print(f"      - [WL:{entry.worklog_id}] {entry.ticketno}")

        before = len(existing_entries)
        existing_entries = [
            e for e in existing_entries
            if e.worklog_id in target_wl_ids or e.worklog_id in orphan_ids
        ]
        print(
            f"    To delete: {len(existing_entries)}/{before} "
            f"(target-day={len(target_wl_ids & {e.worklog_id for e in existing_entries})}, "
            f"orphans={len(orphan_ids)})"
        )

        print()
        print("[6] Status:")
        print(f"    - Existing [WL:] entries to delete: {len(existing_entries)}")
        print(f"    - Tempo worklogs to create: {len(valid_worklogs)}")

        if dry_run:
            print()
            if existing_entries:
                print(f"[DRY-RUN] Would DELETE {len(existing_entries)} existing [WL:] entries:")
                for entry in existing_entries:
                    print(f"    - {entry.ticketno} [WL:{entry.worklog_id}]")
                print()
            print(f"[DRY-RUN] Would CREATE {len(valid_worklogs)} entries:")
            for wl in valid_worklogs:
                print(f"    - {wl.issue_key} | {wl.hours}h | {wl.date} [WL:{wl.worklog_id}]")
            print()
            print("Run with --execute to apply changes.")
        else:
            tracking.open_block(week=week, day=target_day)

            # Delete existing entries
            if existing_entries:
                # Snapshot ids for logging — only those that disappear after
                # the (possibly multi-pass) delete will be logged as deleted.
                ids_before_delete = {
                    (e.worklog_id, e.ticketno) for e in existing_entries
                }

                print()
                print("[6.1] Deleting existing [WL:] entries...")
                await unit4.delete_entries(existing_entries)

                # Re-scan and repeat if needed
                ids_to_kill = target_wl_ids | orphan_ids
                remaining: list[Unit4Entry] = []
                for delete_pass in range(3):
                    print()
                    print(f"    Re-scanning (pass {delete_pass + 1})...")
                    await asyncio.sleep(2)
                    remaining_all = await unit4.extract_entries()
                    # The rescan returns the whole week — keep only the
                    # markers we wanted to delete (target-day + orphans).
                    remaining = [r for r in remaining_all if r.worklog_id in ids_to_kill]
                    if not remaining:
                        print("    All targeted [WL:] entries deleted")
                        break
                    print(f"    {len(remaining)} targeted [WL:] entries still exist, deleting again...")
                    await unit4.delete_entries(remaining)

                # Log what actually disappeared
                still_there_ids = set()
                if remaining:
                    still_there_ids = {r.worklog_id for r in remaining}
                for wl_id, ticket in ids_before_delete:
                    if wl_id not in still_there_ids:
                        tracking.log_delete(wl_id, ticket)

            # Create new entries
            print()
            print("[7] Creating new entries...")
            errors = []
            for wl in valid_worklogs:
                success = await unit4.create_entry(wl)
                if success:
                    tracking.log_create(wl)
                else:
                    errors.append(wl)

            if errors:
                print()
                print(f"[!] Failed to create {len(errors)} entries:")
                for wl in errors:
                    print(f"    - {wl.issue_key} | {wl.hours}h | {wl.date}")

            # Close any open dialog
            print()
            print("[7.5] Closing dialog...")
            frame = await unit4.frame_manager.get_content_frame()
            if await unit4._click_button(frame, "OK"):
                await asyncio.sleep(0.5)
                print("    Dialog closed")
            else:
                print("    No dialog open (or already closed)")

            # Save
            print()
            print("[8] Saving...")
            save_ok = await unit4.save()
            if save_ok:
                print("    Saved!")
            else:
                print("    [!] Click Speichern manually")
                await asyncio.get_event_loop().run_in_executor(None, input)

            capture_ref = None
            if unit4._capture_run_dir is not None and unit4._capture_count > 0:
                capture_ref = str(unit4._capture_run_dir)
            status = "ok" if save_ok and not errors else "fail"
            tracking.close_block(save_status=status, capture_ref=capture_ref)

        # Print final summary
        print()
        print("=" * 50)
        print("SUMMARY")
        print("=" * 50)
        if dry_run:
            print(f"  Mode:     DRY-RUN (no changes made)")
            print(f"  Would delete: {len(existing_entries)} entries")
            print(f"  Would create: {len(valid_worklogs)} entries")
        else:
            deleted_count = len(existing_entries)
            created_count = len(valid_worklogs) - len(errors)
            failed_count = len(errors)
            print(f"  Deleted:  {deleted_count} entries")
            print(f"  Created:  {created_count} entries")
            if failed_count > 0:
                print(f"  Failed:   {failed_count} entries")
        print(f"  Skipped:  {skipped_count} worklogs (no mapping)")

        if skipped_count > 0:
            print()
            print("[!] WARNING: Some worklogs were SKIPPED (not synced to Unit4)!")
            print()
            print("    These worklogs have Tempo accounts without a Unit4 ArbAuft mapping.")
            print("    To sync them: re-run the same command and enter the ArbAuft when")
            print("    prompted (instead of typing SKIP). Your answer is persisted in")
            print("    mapping.json for future runs.")

        print()
        print("[*] Press ENTER to close browser...")
        try:
            await asyncio.get_event_loop().run_in_executor(None, input)
        except EOFError:
            await asyncio.sleep(3)

    print()
    print("[*] Done.")


def week_from_date(date_str: str) -> str:
    """Derive ISO week (YYYYWW) from a YYYY-MM-DD date.

    ISO weeks are unambiguous: every date belongs to exactly one ISO week.
    Sat and Mon of the same calendar week land in the same ISO week, Mon
    of the following week is the next.
    """
    d = datetime.strptime(date_str, "%Y-%m-%d")
    year, week_num, _ = d.isocalendar()
    return f"{year:04d}{week_num:02d}"


def main():
    parser = argparse.ArgumentParser(
        description="Sync Tempo worklogs to Unit4 (one day per invocation)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Check connectivity first
    python sync_tempo_to_unit4.py --check

    # Dry-run for a specific day
    python sync_tempo_to_unit4.py --day 2026-04-29

    # Execute - sync this single day
    python sync_tempo_to_unit4.py --day 2026-04-29 --execute

    # No --day = today (dry-run)
    python sync_tempo_to_unit4.py
        """,
    )

    parser.add_argument(
        "--day",
        metavar="YYYY-MM-DD",
        help="Date to sync. Defaults to today. Pass exactly one date.",
    )
    parser.add_argument(
        "--execute", action="store_true", help="Actually execute changes (default: dry-run)"
    )
    parser.add_argument(
        "--check", action="store_true", help="Check connectivity to all services and exit"
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Interactive setup: walk through config.json fields with prompts and "
        "helper links (Atlassian token page, Tempo token page). Loads existing "
        "values as defaults if config.json already exists.",
    )
    parser.add_argument(
        "--capture",
        dest="capture",
        default=None,
        action=argparse.BooleanOptionalAction,
        help="Enable / disable failure-capture (Playwright trace). "
        "Default: from config.json (debug.capture_enabled), or on if unset.",
    )
    parser.add_argument(
        "--video",
        dest="video",
        default=None,
        action=argparse.BooleanOptionalAction,
        help="Enable / disable browser video recording when capture is on. "
        "Default: from config.json (debug.capture_video), or on if unset.",
    )
    parser.add_argument(
        "--slow",
        type=int,
        default=1,
        metavar="N",
        help="Slow-down factor for Unit4 actions (default 1). "
        "Scales Playwright per-action delay (slow_mo) and click/wait "
        "timeouts by N. Use 2/4/6 when Unit4 is under load and the "
        "default 10s click timeouts start failing.",
    )

    args = parser.parse_args()

    # --init runs before config-load: it's how you create the config in
    # the first place, and it tolerates a missing/invalid existing file.
    if args.init:
        return interactive_init()

    # Load config first (needed for --check and sync)
    config = load_config_safe()
    if config is None:
        return 1

    # Apply CLI overrides for capture / video into the config dict the
    # browser will read. CLI wins over config; config wins over default.
    if args.capture is not None or args.video is not None:
        debug_cfg = dict(config.get("debug") or {})
        if args.capture is not None:
            debug_cfg["capture_enabled"] = args.capture
        if args.video is not None:
            debug_cfg["capture_video"] = args.video
        config["debug"] = debug_cfg

    # Handle --check mode
    if args.check:
        success = check_connectivity(config)
        return 0 if success else 1

    # Resolve target day: --day, or today
    target_day = args.day or datetime.now().strftime("%Y-%m-%d")

    # Validate day format
    if not Patterns.DATE_FORMAT.match(target_day):
        print(f"Error: Invalid date format '{target_day}'. Expected YYYY-MM-DD")
        return 1

    # Derive ISO week from the day
    try:
        week = week_from_date(target_day)
    except ValueError as e:
        print(f"Error: cannot parse date '{target_day}': {e}")
        return 1

    asyncio.run(
        sync(week, target_day, args.execute, config_override=config, slow_factor=args.slow)
    )
    return 0


if __name__ == "__main__":
    exit(main())
