# j2u4

Sync Tempo worklogs to Unit4 Zeiterfassung via Playwright browser automation.

## Disclaimer

**USE AT YOUR OWN RISK.** This software is provided "as is" without warranty of any kind.

CDDS AB and the contributors to this project:
- Make no guarantees about the correctness, reliability, or suitability of this software
- Accept no liability for any damages, data loss, or other issues arising from its use
- Provide no support or maintenance obligations

This tool automates browser interactions with Unit4, which may break at any time due to UI changes. Always verify your time entries manually in Unit4 after syncing.

By using this software, you acknowledge that you are solely responsible for any consequences of its use.

## Requirements

| Requirement | Notes |
|-------------|-------|
| **OS** | Linux / macOS (Windows: use WSL, see below) |
| **uv** | Python package manager ([install](https://docs.astral.sh/uv/getting-started/installation/)) |
| **Network** | VPN if required for Unit4 access |

> **Note:** Python is managed automatically by `uv` — no manual installation needed.

### Windows Users

The setup script (`setup.sh`) requires a Unix shell. On Windows, use
**WSL** (Windows Subsystem for Linux):

```powershell
# 1. Install WSL (run as Administrator)
wsl --install

# 2. Open Ubuntu terminal, then follow Quick Start below
```

## Quick Start

```bash
# 0. Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 1. Clone and setup — this also installs `j2u4` as a global command
git clone <repo-url>
cd j2u4
./setup.sh

# 2. Edit config.json with your API tokens

# 3. Test connectivity
j2u4 --check

# 4. Sync today (dry-run first, then execute)
j2u4 --day $(date -I)
j2u4 --day $(date -I) --execute
```

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

## Setup

### Automatic Setup (recommended)

```bash
./setup.sh
```

This will:
- Verify `uv` is installed
- Install Python + dependencies into a local `.venv` (`uv sync`)
- Install Chromium for Playwright (`uv run playwright install chromium`)
- Create `config.json` from `config.example.json` (if missing)
- Create empty `mapping.json` (if missing)
- **Install `j2u4` as a global command** via `uv tool install --from . j2u4`
  — afterwards `j2u4 …` works from any directory

> **Note:** `uv tool install` puts the executable in `~/.local/bin`. If
> that directory is not in your `$PATH`, `uv` prints a one-line hint
> (typically `uv tool update-shell` or add the directory manually).

### Manual Setup

```bash
# 1. Dependencies (local .venv)
uv sync
uv run playwright install chromium

# 2. Config file
cp config.example.json config.json
echo "{}" > mapping.json

# 3. Install global `j2u4` command
uv tool install --from . j2u4
```

### Updating

After pulling new commits from the repo:

```bash
cd j2u4
git pull
./setup.sh         # idempotent — runs uv tool install --reinstall internally
```

Or, if you only want to refresh the binary without re-running every step:

```bash
uv tool install --from . j2u4 --reinstall
```

### Local venv vs. global tool

There are **two** Python environments after setup:

- The **local `.venv`** (created by `uv sync`) is for development — running
  the test suite (`uv run pytest`) or the diagnostic tools
  (`uv run python tools/inspect_ui.py`).
- The **global `j2u4` command** lives in its own isolated uv-tool venv
  (`~/.local/share/uv/tools/j2u4/`). It works from any working directory
  and is independent of the local `.venv`.

The two do not interfere. Updating the repo + running `./setup.sh` keeps
both in sync.

### API Tokens

You need two API tokens:

- **Jira API Token**: [Create here](https://id.atlassian.com/manage-profile/security/api-tokens)
- **Tempo API Token**: Go to Tempo > Settings > API Integration
  `https://<YOUR-ORG>.atlassian.net/plugins/servlet/ac/io.tempo.jira/tempo-app#!/configuration/api-integration`

Edit `config.json` with your credentials:
```json
{
  "jira": {
    "base_url": "https://<YOUR-ORG>.atlassian.net",
    "user_email": "your-email@example.com",
    "api_token": "your-jira-api-token"
  },
  "tempo": {
    "api_token": "your-tempo-api-token"
  },
  "unit4": {
    "url": "https://ubw.unit4cloud.com/<YOUR-TENANT>/Default.aspx"
  }
}
```

### First run (login)

On first run, Unit4 will prompt for login (2FA). The session is saved to `session.json` for subsequent runs.

## Usage

### Check connectivity first

```bash
j2u4 --check
```

This tests Jira, Tempo, and Unit4 connectivity before syncing.

### Sync a day

```bash
# Dry-run — shows what would happen for a specific day
j2u4 --day 2026-04-29

# Execute — syncs this single day, fully unattended
j2u4 --day 2026-04-29 --execute

# No --day = today
j2u4                         # dry-run for today
j2u4 --execute               # dry-run too: --day still required to act
```

### Capture and video flags

Failure-capture and browser-video defaults come from `config.json`
(`debug.capture_enabled` / `debug.capture_video`, both default to `true`).
Override per-invocation with CLI flags:

```bash
j2u4 --day 2026-04-29 --execute --no-capture          # skip trace+video
j2u4 --day 2026-04-29 --execute --capture --no-video  # trace yes, video no
```

The script syncs **exactly one day per invocation**. The ISO week is derived
from the date — Sat and Mon of the same calendar week land in the same ISO
week, no need to compute it yourself.

Each invocation is atomic: the sync reads the entire week's `[WL:]`
markers, but only deletes-and-recreates the ones whose Tempo worklog id
belongs to the target day. Other days' markers stay untouched. If the run
hangs or fails, only one day is affected; other days can be re-run in
their own invocations.

Each `--execute` run appends a block to `./sync_history.log` (gitignored)
so you can see which markers were deleted and created — see
[Tracking log](#tracking-log) below.

### What the script does

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

### Mapping resolver (3 stages)

For each Tempo account that appears in your worklogs, the resolver tries
in order:

1. **Regex on the Tempo account name** — if the name contains a workorder
   pattern `1018-NNNNN-NNN`, that is the canonical source. Recommended
   pattern: pin the workorder directly in Tempo so no mapping file is
   needed.
2. **Local `mapping.json`** — fallback for accounts
   without an embedded workorder. The file is gitignored and grows
   automatically as you answer prompts.
3. **Interactive prompt** — when neither regex nor file resolved. The
   prompt shows context plus optional "look up here" links from
   `config.json` (`mapping.help_urls`, e.g. your team's Confluence pages
   listing the workorders).

If the regex on the name AND the file disagree (e.g. someone changed the
Tempo account name), the resolver flags the conflict and asks the user
to pick. No silent overrides — drift becomes visible.

### Orphan cleanup (automatic)

Each sync compares Unit4's `[WL:]` markers with the worklog ids Tempo
returns for the whole week. Markers whose worklog no longer exists in
Tempo are removed automatically. This catches the "I deleted a worklog
in Tempo, the marker was stuck in Unit4" case.

### Known limitation: date drift

Deletion is keyed by Tempo worklog id, not by row date in Unit4. If a
Tempo worklog moved from day A to day B but its `[WL:N]` marker still
sits on day A in Unit4, the day-A sync will *not* delete it (id N is no
longer in day A's worklogs). The day-B sync will pick it up wherever it
is in the visible week and recreate it correctly.

### Tracking log

Each `--execute` run appends a block to `./sync_history.log`:

```
=== 2026-04-27T18:30:15 week=202618 day=2026-04-27 ===
DELETE [WL:30007] PROJ-16
CREATE [WL:30007] PROJ-16 0.5h 1018-10175-100
CREATE [WL:30008] PROJ-127 0.75h 1018-10089-108
SAVE ok
```

On failure, `SAVE fail ref=captures/RUN_<ts>` points to the failure capture
folder for diagnosis (see [Failure captures](#failure-captures)).

The file is in `.gitignore` because it contains worklog ids, ticket keys, and
ArbAuft codes — review before sharing externally.

### Entry marker format

Entries are marked with `[WL:xxx]` at the beginning of the text field:
```
[WL:1764] working on concept
```
This allows tracking which Unit4 entries were synced from which Tempo worklog.

## Layout

```
j2u4/
├── pyproject.toml                — package metadata, j2u4 CLI entry point
├── setup.sh                      — one-time setup (deps + uv tool install)
├── README.md
├── config.example.json           — template
├── config.json                   — credentials (gitignored)
├── mapping.json — mapping (gitignored)
├── session.json                  — browser session (gitignored)
├── sync_history.log              — append-only per-run log (gitignored)
├── src/j2u4/
│   ├── cli.py                    — argparse + sync orchestration (j2u4 cmd)
│   ├── browser.py                — Playwright Unit4 automation
│   ├── clients.py                — Jira and Tempo API clients
│   ├── mapping_resolver.py       — regex → file → unresolved pipeline
│   ├── models.py                 — TempoWorklog, Unit4Entry dataclasses
│   ├── patterns.py               — centralized regex patterns
│   └── utils.py                  — config / mapping / date helpers
├── tests/
│   ├── test_patterns.py          — regex + locale config (51 tests)
│   ├── test_capture_failure.py   — failure-capture helper (4 tests)
│   ├── test_per_day_sync.py      — filter + tracking log (7 tests)
│   ├── test_mapping_resolver.py  — resolver pipeline (7 tests)
│   └── test_jira_connection.py   — manual Jira/Tempo smoke (live)
└── tools/
    ├── inspect_ui.py             — dump Unit4 element attributes
    └── debug_dialog_inputs.py    — dump dialog input IDs
```

## Account mappings

There are three ways the resolver can find the workorder for a Tempo
account, in order of preference:

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

## Troubleshooting

### "config.json not found"
- Run `./setup.sh` to create from template, or
- Copy manually: `cp config.example.json config.json`

### "Authentication failed" / API errors
- Run `j2u4 --check` to diagnose connectivity issues
- Verify your API tokens are correct in `config.json`
- Jira token: Check it's not expired at [Atlassian Account](https://id.atlassian.com/manage-profile/security/api-tokens)
- Tempo token: Regenerate in Tempo Settings > API Integration

### "Cannot connect to Unit4"
- Make sure you're connected to VPN (if required)
- Check the URL in `config.json` is correct

### "Page not loaded" / Add button not found
- The script waits for the page to load, but Unit4 can be slow
- If it times out, try running again

### Duplicate entries
- The script deletes all `[WL:xxx]` entries before creating new ones
- If duplicates appear, run the script again to clean them up

### Session expired
- The script will detect this and prompt for re-login
- If issues persist, delete `session.json` and run again

### A specific day failed
Each invocation handles exactly one day, so failures are isolated. When a
day fails:

1. Check `captures/RUN_*/` — the trace is there.
2. Check `sync_history.log` — the most recent block records `SAVE fail`
   with a back-reference to the capture folder.
3. Open the trace: `uv run playwright show-trace captures/RUN_*/.../trace.zip`
4. Fix the underlying cause and re-run `j2u4 --day YYYY-MM-DD --execute`.

The re-run will detect the partial state (some `[WL:]` markers from the
failed run, some missing) and bring the day to consistency by deleting
and recreating the target day's markers.

### Unit4 language
- The browser automation works with both **German** and **English** Unit4 UI
- Most selectors use stable element IDs; remaining text-based selectors try both languages automatically
- If you encounter issues with a different UI language, run the UI inspector and share the output:
  ```bash
  uv run python tools/inspect_ui.py
  ```
  This opens Unit4, scans all UI elements, and saves their HTML attributes to `ui_inspection.json`.

## Failure captures

When something goes wrong during the browser automation (Add button missing, dialog freezes, hours fill fails, save errors out), the script writes a **Playwright trace** for that specific failure. Each capture is a self-contained folder you can ZIP up and send for diagnosis.

### Where captures land

```
captures/
└── RUN_2026-04-28T14-23-05/
    ├── 2026-04-28T14-23-49_CREATE_PROJ-123/
    │   ├── trace.zip       # Playwright trace — open in Trace Viewer
    │   ├── context.json    # worklog data, step, exception, recent page errors
    │   └── README.txt      # short instructions
    └── *.webm              # browser video for the whole run (if capture_video)
```

If the run finishes without any failure, the whole `RUN_*` folder is deleted automatically — no clutter from successful runs.

### Opening a trace

```bash
uv run playwright show-trace captures/RUN_<ts>/<failure-folder>/trace.zip
```

The Trace Viewer shows every action with before/after DOM snapshots, network and console logs, and a screenshot timeline. This is usually enough to see *exactly* what the browser was doing when the failure happened.

### Privacy warning

Traces and videos contain **DOM content of the entire week** that was visible during the failure — not only the failing worklog. Other tickets, descriptions, customer names may be present. Review captures before sharing externally.

### Disabling captures

Set in `config.json`:

```json
"debug": {
  "capture_enabled": false
}
```

The whole `debug` block is optional; defaults are `capture_enabled: true`, `capture_dir: "./captures"`, `capture_cap: 10`, `capture_video: true`.

## Testing

Since Unit4 is a live enterprise system with no sandbox or staging environment, full end-to-end tests are not feasible. The test suite therefore focuses on what **can** be verified offline:

```bash
# Install dev dependencies (once)
uv sync --extra dev

# Run tests
uv run pytest
```

**What is tested:**
- Regex patterns (day labels, worklog markers, ticket keys, ArbAuft codes) against both German and English inputs
- Locale configuration consistency (both locales define the same keys, non-empty values)

**What requires manual verification:**
- Browser automation against a live Unit4 instance (`j2u4 --check`, then `j2u4 --day YYYY-MM-DD --execute`)
- Session handling, login flow, 2FA

## Security

- **Never commit** `config.json` or `session.json`
- These files are in `.gitignore`
- Use `config.example.json` as template

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
