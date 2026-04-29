"""
Sync Tempo worklogs to Unit4.

Usage:
    # Check connectivity first
    python sync_tempo_to_unit4.py --check

    # Dry-run (default) - shows what would happen
    python sync_tempo_to_unit4.py 202605

    # Execute - actually creates entries
    python sync_tempo_to_unit4.py 202605 --execute

    # With cutover date (only sync from this date onwards)
    python sync_tempo_to_unit4.py 202605 --cutover 2026-01-29 --execute
"""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

import requests

from clients import JiraClient, TempoClient, ACCOUNT_FIELD, ApiError
from models import TempoWorklog
from patterns import Patterns
from unit4_browser import Unit4Browser
from utils import (
    get_current_week,
    get_week_dates,
    load_config_safe,
    load_mapping,
    save_mapping,
)


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

    def open_block(self, mode: str, week: str, day: str | None) -> None:
        ts = datetime.now().isoformat(timespec="seconds")
        day_str = day or "all"
        self._append(f"\n=== {ts} mode={mode} week={week} day={day_str} ===\n")

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
        print("NOTE: No account mappings found yet.")
        print("      You have two options:")
        print("      1. Run sync and enter mappings when prompted")
        print("      2. Auto-build from Unit4 history:")
        print("         python build_mapping_from_history.py")
    print("=" * 50)
    print()

    return all_ok


def process_worklogs(
    config: dict, raw_worklogs: list[dict], mapping: dict
) -> tuple[list[TempoWorklog], list[TempoWorklog]]:
    """Process raw Tempo worklogs, fetch Jira details, apply mapping.

    Returns:
        - valid_worklogs: Worklogs with complete mapping
        - unmapped_worklogs: Worklogs with unknown account
    """
    valid_worklogs = []
    unmapped_worklogs = []
    issue_cache: dict[int, dict] = {}

    jira = JiraClient(config)

    print(f"[*] Processing {len(raw_worklogs)} worklogs...")

    for wl in raw_worklogs:
        worklog_id = wl["tempoWorklogId"]
        issue_id = wl.get("issue", {}).get("id")
        date = wl["startDate"]
        hours = wl["timeSpentSeconds"] / 3600
        description = wl.get("description", "")

        # Fetch issue details if not cached
        if issue_id and issue_id not in issue_cache:
            issue_data = jira.get_issue_details(issue_id)
            if issue_data:
                fields = issue_data.get("fields", {})
                account_field = fields.get(ACCOUNT_FIELD)

                if isinstance(account_field, dict):
                    account_key = str(account_field.get("key") or account_field.get("id") or "")
                    account_name = account_field.get("name") or account_field.get("value") or ""
                else:
                    account_key = ""
                    account_name = ""

                issue_cache[issue_id] = {
                    "key": issue_data.get("key", f"ID:{issue_id}"),
                    "summary": fields.get("summary", "")[:100],
                    "account_key": account_key,
                    "account_name": account_name,
                }
            else:
                issue_cache[issue_id] = {
                    "key": f"ID:{issue_id}",
                    "summary": "?",
                    "account_key": "",
                    "account_name": "",
                }

        issue_info = issue_cache.get(issue_id, {})
        account_key = issue_info.get("account_key", "")

        # Apply mapping
        arbauft = None
        if account_key and account_key in mapping:
            arbauft = mapping[account_key]["unit4_arbauft"]

        worklog = TempoWorklog(
            worklog_id=worklog_id,
            issue_id=issue_id,
            issue_key=issue_info.get("key", "?"),
            issue_summary=issue_info.get("summary", "?"),
            date=date,
            hours=hours,
            description=description,
            account_key=account_key,
            account_name=issue_info.get("account_name", ""),
            arbauft=arbauft,
        )

        if arbauft:
            valid_worklogs.append(worklog)
        else:
            unmapped_worklogs.append(worklog)

    return valid_worklogs, unmapped_worklogs


def ask_for_arbauft(worklog: TempoWorklog, mapping: dict) -> str | None:
    """Interactively ask user for ArbAuft for an unmapped worklog."""
    print()
    print(f"  Unknown Account: {worklog.account_key} ({worklog.account_name})")
    print(f"    Ticket: {worklog.issue_key}")
    print(f"    Summary: {worklog.issue_summary[:60]}")
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


async def sync_semi_auto(week: str, valid_worklogs: list, config: dict):
    """Semi-automatic flow: per worklog, fill non-hours fields, then wait
    for user to enter hours and click OK in the browser."""
    print()
    print("=" * 70)
    print(f"SEMI-AUTO SYNC | Week {week} | {len(valid_worklogs)} entries")
    print("=" * 70)
    print()
    print("Ablauf pro Eintrag:")
    print("  1. Skript zeigt Details")
    print("  2. DU klickst im Browser: Ergänzen + Zoom auf der neuen Zeile")
    print("  3. ENTER in der Shell → Skript füllt ArbAuft/Text/Ticketno")
    print("  4. DU wählst Aktivität (TEMPO), trägst Stunden ein, klickst OK")
    print("  5. ENTER in der Shell → nächster Eintrag")
    print()
    print("Steuerung: [ENTER]=weiter, s=skip, q=quit")
    print()

    async with Unit4Browser(config) as unit4:
        await unit4.navigate_to_zeiterfassung()
        if not await unit4.set_week(week):
            await asyncio.sleep(2)
            await unit4.set_week(week)
        if not await unit4.wait_for_ready():
            print("[!] Woche ist nicht editierbar (eingereicht?). Abbruch.")
            return

        sorted_wls = sorted(valid_worklogs, key=lambda w: (w.date, w.issue_key))
        total = len(sorted_wls)
        done = 0
        skipped = 0

        for idx, wl in enumerate(sorted_wls, 1):
            description = (wl.description or wl.issue_summary or "").strip()
            text_preview = f"[WL:{wl.worklog_id}] {description[:60]}"
            print(f"[{idx}/{total}] {wl.issue_key} | {wl.hours:.2f}h | {wl.date}")
            print(f"        ArbAuft : {wl.arbauft}")
            print(f"        Aktivität: TEMPO")
            print(f"        Text    : {text_preview}")

            choice = (await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("        > Ergänzen+Zoom im Browser, dann [ENTER]=fill, s=skip, q=quit: ")
            )).strip().lower()
            if choice == "q":
                print("        Abbruch durch User.")
                break
            if choice == "s":
                print("        übersprungen.")
                skipped += 1
                print()
                continue

            print("        Skript füllt Dialog-Felder...", flush=True)
            result = await unit4.fill_open_dialog(wl)

            def mark(ok) -> str:
                if ok is None:
                    return "MANUELL"
                return "OK  " if ok else "FAIL"

            print(f"          ArbAuft  : {mark(result['arbauft'])}")
            print(f"          Aktivität: {mark(result['aktivitaet'])}  (PRÜFEN: TEMPO, ggf. korrigieren)")
            print(f"          Text     : {mark(result['text'])}")
            print(f"          Ticketno : {mark(result['ticketno'])}")
            print(f"          Stunden  : {mark(result['hours'])}  (PRÜFEN im Browser!)")

            if not result["dialog_open"]:
                print("        [!] Dialog konnte nicht geöffnet werden. Bitte manuell anlegen.")
                input("        > ENTER wenn Eintrag manuell fertig ist: ")
                done += 1
                print()
                continue

            failed_fields = [k for k in ("arbauft", "text", "ticketno", "hours") if not result[k]]
            if failed_fields:
                print(f"        [!] BITTE NACHTRAGEN: {', '.join(failed_fields)}")
            print(f"        >>> Im Browser prüfen, ggf. korrigieren, OK klicken")
            input("        > ENTER wenn Dialog im Browser geschlossen: ")
            done += 1
            print()

        print()
        print("=" * 50)
        print("SEMI-AUTO SUMMARY")
        print("=" * 50)
        print(f"  Bearbeitet: {done}")
        print(f"  Skipped:    {skipped}")
        print(f"  Verbleibend: {total - done - skipped}")
        print()
        print("=" * 60)
        print(">>> WICHTIG: SAVE/SPEICHERN im Browser klicken!")
        print(">>> Ohne Save sind ALLE Einträge weg sobald der Browser schließt.")
        print("=" * 60)
        loop = asyncio.get_event_loop()
        while True:
            try:
                answer = (await loop.run_in_executor(
                    None, lambda: input("    >>> Hast du SAVE geklickt? [j=ja, schließen / n=nein, warten]: ")
                )).strip().lower()
            except EOFError:
                await asyncio.sleep(5)
                continue
            if answer in ("j", "ja", "y", "yes"):
                print("    OK — Browser wird geschlossen.")
                break
            print("    Browser bleibt offen. Klick SAVE, dann nochmal abfragen.")


async def sync(week: str, cutover: str | None, execute: bool, end: str | None = None, limit: int | None = None, days: list[str] | None = None):
    """Main sync function."""
    dry_run = not execute
    mode = "EXECUTE" if execute else "DRY-RUN"

    print()
    print("=" * 70)
    print(f"SYNC TEMPO -> UNIT4 | Week {week} | Mode: {mode}")
    print("=" * 70)
    print()

    # Load config and mapping
    config = load_config_safe()
    if config is None:
        return

    mapping = load_mapping()
    unit4_url = config.get("unit4", {}).get("url")
    if not unit4_url:
        print("[!] Error: unit4.url not configured in config.json")
        return
    print(f"[*] Loaded mapping with {len(mapping)} accounts")

    # Get week dates
    date_from, date_to = get_week_dates(week)
    print(f"[*] Week {week}: {date_from} to {date_to}")

    if cutover:
        date_from = cutover
        print(f"[*] Cutover start: {date_from}")
    if end:
        date_to = end
        print(f"[*] Extended end: {date_to}")

    # Fetch Tempo worklogs
    print()
    print(f"[1] Fetching Tempo worklogs ({date_from} to {date_to})...")

    try:
        jira = JiraClient(config)
        tempo = TempoClient(config)

        account_id = jira.get_my_account_id()
        raw_worklogs = tempo.fetch_worklogs(account_id, date_from, date_to)
    except ApiError as e:
        print()
        print(f"[!] API Error: {e}")
        print()
        print("    Run 'python sync_tempo_to_unit4.py --check' to diagnose the issue.")
        return

    print(f"    Found {len(raw_worklogs)} worklogs")

    # Process worklogs
    print()
    print("[2] Processing worklogs (Jira lookup + mapping)...")
    valid_worklogs, unmapped_worklogs = process_worklogs(config, raw_worklogs, mapping)
    print(f"    Valid: {len(valid_worklogs)}, Unmapped: {len(unmapped_worklogs)}")

    # Handle unmapped worklogs interactively
    skipped_count = 0
    if unmapped_worklogs:
        print()
        print("[!] Found unmapped worklogs. Enter ArbAuft or SKIP:")
        for wl in unmapped_worklogs:
            arbauft = ask_for_arbauft(wl, mapping)
            if arbauft:
                wl.arbauft = arbauft
                valid_worklogs.append(wl)
            else:
                skipped_count += 1

    # Filter to specific days if requested
    if days:
        day_set = set(days)
        before = len(valid_worklogs)
        valid_worklogs = [w for w in valid_worklogs if w.date in day_set]
        print(f"[!] --day filter: {len(valid_worklogs)}/{before} entries match {sorted(day_set)}")

    # Apply limit (for testing with first N entries)
    if limit is not None and len(valid_worklogs) > limit:
        valid_worklogs = sorted(valid_worklogs, key=lambda x: (x.date, x.issue_key))[:limit]
        print(f"[!] --limit {limit}: only first {limit} entries will be synced")

    # Show summary
    print()
    print("[3] Summary of worklogs to sync:")
    total_hours = 0
    for wl in sorted(valid_worklogs, key=lambda x: (x.date, x.issue_key)):
        print(
            f"    {wl.date} | {wl.hours:5.2f}h | {wl.issue_key:<15} | {wl.arbauft} [WL:{wl.worklog_id}]"
        )
        total_hours += wl.hours
    print(f"    {'─' * 60}")
    print(f"    Total: {total_hours:.2f}h across {len(valid_worklogs)} entries")

    if not valid_worklogs:
        print()
        print("[*] No worklogs to sync. Done.")
        return

    target_day = days[0] if days else None
    mode = "day-auto" if target_day else "week-bulk"
    tracking = TrackingLog()

    # Connect to Unit4
    print()
    print("[4] Connecting to Unit4...")

    async with Unit4Browser(config) as unit4:
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
        print(f"    Found {len(existing_entries)} synced entries")

        # In per-day mode, only delete entries whose worklog_id belongs to the
        # target day's Tempo worklogs. Markers from other days stay untouched.
        if target_day:
            target_wl_ids = {wl.worklog_id for wl in valid_worklogs}
            before = len(existing_entries)
            existing_entries = [
                e for e in existing_entries if e.worklog_id in target_wl_ids
            ]
            print(
                f"    [day-auto] Filtered existing entries by target day: "
                f"{len(existing_entries)}/{before} match worklog ids of {target_day}"
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
            tracking.open_block(mode=mode, week=week, day=target_day)

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
                for delete_pass in range(3):
                    print()
                    print(f"    Re-scanning (pass {delete_pass + 1})...")
                    await asyncio.sleep(2)
                    remaining = await unit4.extract_entries()
                    if not remaining:
                        print("    All [WL:] entries deleted successfully")
                        break
                    # In day-auto, the rescan returns the whole week — filter
                    # again so we only retry our target day's worklogs.
                    if target_day:
                        target_wl_ids = {wl.worklog_id for wl in valid_worklogs}
                        remaining = [r for r in remaining if r.worklog_id in target_wl_ids]
                        if not remaining:
                            print("    All target-day [WL:] entries deleted")
                            break
                    print(f"    {len(remaining)} [WL:] entries still exist, deleting again...")
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
            print("    To sync them, you need to add the mapping. Options:")
            print()
            print("    1. Run sync again and enter the ArbAuft when prompted")
            print("       (instead of typing SKIP, enter the ArbAuft code)")
            print()
            print("    2. Auto-build mappings from your Unit4 history:")
            print("       python build_mapping_from_history.py")
            print()
            print("    3. Manually edit account_to_arbauft_mapping.json")

        print()
        print("[*] Press ENTER to close browser...")
        try:
            await asyncio.get_event_loop().run_in_executor(None, input)
        except EOFError:
            await asyncio.sleep(3)

    print()
    print("[*] Done.")


def main():
    parser = argparse.ArgumentParser(
        description="Sync Tempo worklogs to Unit4",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Check connectivity first
    python sync_tempo_to_unit4.py --check

    # Dry-run (default) - shows what would happen
    python sync_tempo_to_unit4.py 202605

    # Execute - actually creates entries
    python sync_tempo_to_unit4.py 202605 --execute

    # With cutover date (only sync from this date onwards)
    python sync_tempo_to_unit4.py 202605 --cutover 2026-01-29 --execute
        """,
    )

    parser.add_argument(
        "week", nargs="?", default=None, help="Week to sync (YYYYWW), default: current week"
    )
    parser.add_argument(
        "--execute", action="store_true", help="Actually execute changes (default: dry-run)"
    )
    parser.add_argument("--cutover", help="Only sync from this date onwards (YYYY-MM-DD)")
    parser.add_argument("--end", help="Extend week end date to this date (YYYY-MM-DD)")
    parser.add_argument(
        "--check", action="store_true", help="Check connectivity to all services and exit"
    )
    parser.add_argument("--limit", type=int, help="Only sync the first N entries (for testing)")
    parser.add_argument(
        "--day",
        action="append",
        metavar="YYYY-MM-DD",
        help="Sync only worklogs on this date. With --execute: fully automatic single-day sync. "
        "Without --execute: dry-run preview. Pass at most one --day per invocation.",
    )

    args = parser.parse_args()

    # Load config first (needed for --check and sync)
    config = load_config_safe()
    if config is None:
        return 1

    # Handle --check mode
    if args.check:
        success = check_connectivity(config)
        return 0 if success else 1

    week = args.week or get_current_week()

    # Validate week format
    if not Patterns.WEEK_FORMAT.match(week):
        print(f"Error: Invalid week format '{week}'. Expected YYYYWW (e.g., 202605)")
        return 1

    # Validate cutover format
    if args.cutover and not Patterns.DATE_FORMAT.match(args.cutover):
        print(f"Error: Invalid cutover format '{args.cutover}'. Expected YYYY-MM-DD")
        return 1

    # Per-day mode is single-day only — multi-day in one invocation is no
    # longer supported (atomicity guarantees per call only).
    if args.day and len(args.day) > 1:
        print(
            f"Error: pass at most one --day per invocation (got {len(args.day)}). "
            "Run the script once per day."
        )
        return 1

    asyncio.run(sync(week, args.cutover, args.execute, args.end, args.limit, args.day))
    return 0


if __name__ == "__main__":
    exit(main())
