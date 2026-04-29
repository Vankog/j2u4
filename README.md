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

The shell scripts (`setup.sh`, `sync`, `build-mapping`) require a Unix shell.
On Windows, use **WSL** (Windows Subsystem for Linux):

```powershell
# 1. Install WSL (run as Administrator)
wsl --install

# 2. Open Ubuntu terminal, then follow Quick Start below
```

## Quick Start

```bash
# 0. Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 1. Clone and setup
git clone <repo-url>
cd j2u4
./setup.sh

# 2. Edit config.json with your API tokens

# 3. Test connectivity
./sync --check

# 4. Sync a day (dry-run first, then execute)
./sync --day 2026-04-29
./sync --day 2026-04-29 --execute
```

## How it works

```
Tempo API ──→ Worklogs (date, hours, issue_id)
    │
    ▼
Jira API ──→ Issue Details (key, summary, Account field)
    │
    ▼
Mapping ──→ Tempo Account → Unit4 ArbAuft
    │
    ▼
Playwright ──→ Unit4 Zeiterfassung (browser automation)
```

## Setup

### Automatic Setup (recommended)

```bash
./setup.sh
```

This will:
- Install Python and all dependencies (via `uv`)
- Install Chromium for browser automation
- Create `config.json` from template

### Manual Setup

1. **Install dependencies**
   ```bash
   uv sync
   uv run playwright install chromium
   ```

2. **Create config file**
   ```bash
   cp config.example.json config.json
   ```

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
./sync --check
```

This tests Jira, Tempo, and Unit4 connectivity before syncing.

### Sync a day

```bash
# Dry-run — shows what would happen for a specific day
./sync --day 2026-04-29

# Execute — syncs this single day, fully unattended
./sync --day 2026-04-29 --execute

# No --day = today
./sync                       # dry-run for today
./sync --execute             # ← error: --day required for --execute
```

The script syncs **exactly one day per invocation**. The ISO week is derived
from the date — Sat and Mon of the same calendar week land in the same ISO
week, no need to compute it yourself.

Each invocation is atomic: the script reads the entire week's `[WL:]`
markers, but only deletes-and-recreates the ones whose Tempo worklog id
belongs to the target day. Other days' markers stay untouched. If the run
hangs or fails, only one day is affected; other days can be re-run in
their own invocations.

Each `--execute` run appends a block to `./sync_history.log` (gitignored)
so you can see which markers were deleted and created — see
[Tracking log](#tracking-log) below.

### What the script does

1. Derives the ISO week from `--day` (e.g. `2026-04-29` → `202618`)
2. Fetches Tempo worklogs for that single day
3. Looks up Jira issues to get the Account field
4. Maps Account → Unit4 ArbAuft code
5. Opens Unit4 (browser is visible — you can watch)
6. Reads existing `[WL:]` markers in the current week
7. Filters them to the target day's worklog ids
8. **Deletes** these markers
9. **Creates** fresh entries from Tempo
10. **Saves**, then writes the result to `sync_history.log`

### Known limitation: date drift

Deletion is keyed by Tempo worklog id, not by row date in Unit4. If a Tempo
worklog moved from day A to day B but its `[WL:N]` marker still sits on day
A in Unit4, the day-A sync will *not* delete it (id N is no longer among
day A's worklogs). The day-B sync will pick it up wherever it is in the
visible week and recreate it correctly. Truly orphan markers (Tempo worklog
deleted) need manual cleanup in Unit4 — there is no bulk-cleanup mode in
this script.

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

## Files

| File | Purpose |
|------|---------|
| `setup.sh` | One-time setup (creates venv, installs dependencies) |
| `sync` | Wrapper script for syncing (use this!) |
| `build-mapping` | Wrapper script for building mappings |
| `sync_tempo_to_unit4.py` | Main sync entry point (CLI) |
| `unit4_browser.py` | Playwright-based Unit4 browser automation |
| `build_mapping_from_history.py` | Build account→arbauft mapping from Unit4 history |
| `clients.py` | Jira and Tempo API clients |
| `models.py` | Dataclasses (TempoWorklog, Unit4Entry, SyncConfig, …) |
| `patterns.py` | Centralized regex patterns |
| `utils.py` | Shared helpers (config loading, dates, …) |
| `inspect_ui.py` | UI inspector — dumps Unit4 element attributes to `ui_inspection.json` |
| `debug_dialog_inputs.py` | One-shot dialog inspector — dumps input IDs from the Add+Zoom dialog |
| `test_patterns.py` | Offline pytest suite (regex + locale config) |
| `test_capture_failure.py` | Offline tests for the failure-capture helper |
| `test_per_day_sync.py` | Offline tests for per-day filter + tracking log |
| `test_jira_connection.py` | Manual Jira/Tempo connectivity test script |
| `config.json` | Credentials (gitignored!) |
| `config.example.json` | Template for config.json |
| `account_to_arbauft_mapping.json` | Account to ArbAuft mapping (gitignored!) |
| `session.json` | Browser session (gitignored!) |
| `sync_history.log` | Append-only per-run log (gitignored!) |

## Account Mappings

The script needs to know which Tempo Account maps to which Unit4 ArbAuft code.
This mapping is stored in `account_to_arbauft_mapping.json`.

### Option 1: Auto-build from Unit4 history (recommended)

If you already have time entries in Unit4, the script can learn the mappings:

```bash
# Scan last 8 weeks (default)
./build-mapping

# Scan last 12 weeks
./build-mapping --weeks 12

# Scan specific range
./build-mapping --from 202601 --to 202610
```

This opens Unit4, scans the specified weeks, and builds the mapping automatically.

### Option 2: Enter mappings during sync

When the script encounters an unknown Tempo account, it will prompt you:

```
Unknown Account: 42 (ACME - Development)
  Ticket: ACME-1234
  Summary: Fix deployment pipeline

Enter ArbAuft (e.g., 1234-56789-001) or SKIP to skip:
```

Enter the ArbAuft code and it will be saved for future use.

### Option 3: Manual editing

Edit `account_to_arbauft_mapping.json` directly:

```json
{
  "42": {
    "unit4_arbauft": "1234-56789-001",
    "tempo_name": "ACME - Development",
    "sample_ticket": "ACME-1234"
  }
}
```

### Finding the right ArbAuft code

The ArbAuft code (e.g., `1234-56789-001`) is visible in Unit4 when you create a time entry.
It's the "ArbAuft" field in the entry form.

## Command Reference

| Command | Description |
|---------|-------------|
| `./setup.sh` | Initial setup (run once after cloning) |
| `./sync --check` | Test connectivity to Jira, Tempo, Unit4 |
| `./sync --day YYYY-MM-DD --execute` | Sync this single day |
| `./sync --day YYYY-MM-DD` | Dry-run for that day |
| `./sync` | Dry-run for today |
| `./build-mapping` | Build mappings from last 8 weeks |
| `./build-mapping --weeks N` | Build mappings from last N weeks |
| `./build-mapping --from YYYYWW --to YYYYWW` | Build mappings from specific range |

## Troubleshooting

### "config.json not found"
- Run `./setup.sh` to create from template, or
- Copy manually: `cp config.example.json config.json`

### "Authentication failed" / API errors
- Run `./sync --check` to diagnose connectivity issues
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
4. Fix the underlying cause and re-run `./sync --day YYYY-MM-DD --execute`.

The re-run will detect the partial state (some `[WL:]` markers from the
failed run, some missing) and bring the day to consistency by deleting
and recreating the target day's markers.

### Unit4 language
- The browser automation works with both **German** and **English** Unit4 UI
- Most selectors use stable element IDs; remaining text-based selectors try both languages automatically
- If you encounter issues with a different UI language, run the UI inspector and share the output:
  ```bash
  uv run python inspect_ui.py
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
- Browser automation against a live Unit4 instance (`./sync --check`, then `./sync --day YYYY-MM-DD --execute`)
- Session handling, login flow, 2FA

## Security

- **Never commit** `config.json` or `session.json`
- These files are in `.gitignore`
- Use `config.example.json` as template

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
