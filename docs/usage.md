# Usage

## How it works

```
Tempo API ──→ Tempo Account (per-account /worklogs endpoint)
    │            │
    │            ▼
    │       Mapping resolver: regex(account.name) → mapping.json → prompt
    │            │
    ▼            ▼
Worklogs ──→ Unit4 ArbAuft
    │
    ▼
Playwright ──→ Unit4 Zeiterfassung (browser automation)
```

The Jira API is only used to look up issue summaries (best-effort, can
404 silently for tickets you do not have permission to read — the sync
continues with an empty summary).

## Check connectivity first

```bash
j2u4 --check
```

This tests Jira, Tempo, and Unit4 connectivity before syncing.

## Sync a day

```bash
# Dry-run — shows what would happen for a specific day
j2u4 --day 2026-04-29

# Execute — syncs this single day, fully unattended
j2u4 --day 2026-04-29 --execute

# No --day = today
j2u4                         # dry-run for today
j2u4 --execute               # dry-run too: --day still required to act
```

The script syncs **exactly one day per invocation**. The ISO week is derived
from the date — Sat and Mon of the same calendar week land in the same ISO
week, no need to compute it yourself.

Each invocation is atomic: the sync reads the entire week's `[WL:]`
markers, but only deletes-and-recreates the ones whose Tempo worklog id
belongs to the target day. Other days' markers stay untouched. If the run
hangs or fails, only one day is affected; other days can be re-run in
their own invocations.

Each `--execute` run appends a block to `sync_history.log` in the
user-config directory (gitignored) so you can see which markers were
deleted and created — see [Tracking log](#tracking-log) below.

## Capture and video flags

Failure-capture and browser-video defaults come from `config.json`
(`debug.capture_enabled` / `debug.capture_video`, both default to `true`).
Override per-invocation with CLI flags:

```bash
j2u4 --day 2026-04-29 --execute --no-capture          # skip trace+video
j2u4 --day 2026-04-29 --execute --capture --no-video  # trace yes, video no
```

## Slow Unit4 (`--slow N`)

When Unit4 is under load (peak hours, end of month, etc.) the default
10-second click timeouts can start to fail. Pass `--slow N` to scale
both Playwright's per-action delay and the click/wait timeouts by `N`:

```bash
j2u4 --day 2026-04-29 --execute --slow 2   # 2× — moderate slowdown
j2u4 --day 2026-04-29 --execute --slow 4   # 4× — busy server hours
j2u4 --day 2026-04-29 --execute --slow 6   # 6× — when even patience runs out
```

Default is `--slow 1` (no change). The factor scales:
- Playwright `slow_mo` (default 100 ms per action → 200/400/600 ms)
- Click and wait timeouts (default 10 s → 20/40/60 s)

The blanket `asyncio.sleep(...)` calls inside the script are **not**
scaled — they're already conservative. `--slow N` mainly buys patience
for individual UI events without making the whole sync proportionally
slower.

## What the script does

1. Derives the ISO week from `--day` (e.g. `2026-04-29` → `202618`)
2. Fetches Tempo worklogs **per Tempo account** (no Jira-Issue lookup
   for account resolution → permission-friendly)
3. Resolves each account's ArbAuft via the resolver pipeline (regex on
   the account name → mapping.json → interactive prompt)
4. Opens Unit4 (browser is visible — you can watch)
5. Reads existing `[WL:]` markers in the current week
6. Identifies two delete-sets:
   - **Target-day markers** (their worklog id matches today's Tempo worklogs)
   - **Orphans** (their worklog id no longer exists in this week's Tempo)
7. **Deletes** both sets
8. **Creates** fresh entries from Tempo's target-day worklogs
9. **Saves**, then writes the result to `sync_history.log`

## Mapping resolver (3 stages)

For each Tempo account that appears in your worklogs, the resolver tries
in order:

1. **Regex on the Tempo account name** — if the name contains a workorder
   pattern `1018-NNNNN-NNN`, that is the canonical source. Recommended
   pattern: pin the workorder directly in Tempo so no mapping file is
   needed.
2. **Local `mapping.json`** — fallback for accounts without an embedded
   workorder. The file is gitignored and grows automatically as you
   answer prompts.
3. **Interactive prompt** — when neither regex nor file resolved. The
   prompt shows context plus optional "look up here" links from
   `config.json` (`mapping.help_urls`, e.g. your team's Confluence pages
   listing the workorders).

If the regex on the name AND the file disagree (e.g. someone changed the
Tempo account name), the resolver flags the conflict and asks the user
to pick. No silent overrides — drift becomes visible.

### 1. Workorder in the Tempo account name (recommended)

Pin the workorder directly in the Tempo account name, e.g.:

```
ACME - Operations - Cloud (1018-12345-001)
```

The resolver picks this up automatically — **no mapping file needed** for
that account. This is the lowest-maintenance source: there is no separate
file to keep in sync, and the workorder is visible to anyone in Tempo.

### 2. Local `mapping.json`

For accounts whose name does not include a workorder, the resolver falls
back to a local JSON file (gitignored) keyed by Tempo account id:

```json
{
  "42": {
    "unit4_arbauft": "1234-56789-001",
    "tempo_name": "ACME - Development",
    "sample_ticket": "ACME-1234"
  }
}
```

The file grows automatically as you answer prompts during sync. Manual
editing is fine.

### 3. Interactive prompt with help links

If neither name nor file resolves the account, the sync prompts:

```
  Unmapped Tempo account: 42 (ACME - Development)
    Ticket : ACME-1234
    Summary: Fix deployment pipeline
  Look up the workorder in:
      - https://your-domain.atlassian.net/wiki/spaces/.../Customer+Projects
      - https://your-domain.atlassian.net/wiki/spaces/.../Internal+Projects

  Enter ArbAuft (e.g., 1234-56789-001) or SKIP to skip:
```

The "Look up the workorder in:" URLs come from
`config.mapping.help_urls` (optional). Type the workorder you find — it's
persisted into `mapping.json` for future runs.

### Conflicts

If the Tempo account name says one workorder and `mapping.json` says
another, the resolver shows both and asks you to pick. There is no
silent override — drift between the two sources is always made visible.

### Finding the right ArbAuft code

The ArbAuft code (e.g., `1234-56789-001`) is visible in Unit4 when you
create a time entry, in the "ArbAuft" field. Your team typically also
keeps a list (Confluence, internal wiki, …) — point `config.mapping.help_urls`
at it so the prompt links there.

## Orphan cleanup (automatic)

Each sync compares Unit4's `[WL:]` markers with the worklog ids Tempo
returns for the whole week. Markers whose worklog no longer exists in
Tempo are removed automatically. This catches the "I deleted a worklog
in Tempo, the marker was stuck in Unit4" case.

## Known limitation: date drift

Deletion is keyed by Tempo worklog id, not by row date in Unit4. If a
Tempo worklog moved from day A to day B but its `[WL:N]` marker still
sits on day A in Unit4, the day-A sync will *not* delete it (id N is no
longer in day A's worklogs). The day-B sync will pick it up wherever it
is in the visible week and recreate it correctly.

## Tracking log

Each `--execute` run appends a block to `sync_history.log` in the
user-config directory (`~/.config/j2u4/sync_history.log` on Linux/macOS,
`%APPDATA%\j2u4\sync_history.log` on Windows; override via
`$J2U4_CONFIG_DIR`):

```
=== 2026-04-27T18:30:15 week=202618 day=2026-04-27 ===
DELETE [WL:30007] PROJ-16
CREATE [WL:30007] PROJ-16 0.5h 1018-10175-100
CREATE [WL:30008] PROJ-127 0.75h 1018-10089-108
SAVE ok
```

On failure, `SAVE fail ref=captures/RUN_<ts>` points to the failure
capture folder for diagnosis (see
[troubleshooting.md](troubleshooting.md)).

The file is in `.gitignore` because it contains worklog ids, ticket
keys, and ArbAuft codes — review before sharing externally.

## Entry marker format

Entries are marked with `[WL:xxx]` at the beginning of the text field:

```
[WL:1764] working on concept
```

This allows tracking which Unit4 entries were synced from which Tempo worklog.

## Command Reference

| Command | Description |
|---------|-------------|
| `./setup.sh` | Initial setup (deps + Chromium + `j2u4` install via `uv tool`) |
| `j2u4 --check` | Test connectivity to Jira, Tempo, Unit4 |
| `j2u4 --day YYYY-MM-DD --execute` | Sync this single day |
| `j2u4 --day YYYY-MM-DD` | Dry-run for that day |
| `j2u4` | Dry-run for today |
| `j2u4 ... --no-capture` | Disable failure-capture for this run (override config) |
| `j2u4 ... --no-video` | Disable video recording for this run (override config) |
| `j2u4 ... --slow N` | Scale Playwright slow_mo + click/wait timeouts by N (use 2/4/6 for slow Unit4) |
| `j2u4 --init` | Interactive setup of config.json (helper links + hidden token input) |
