# Development

## Layout

```
j2u4/
├── pyproject.toml                — package metadata, j2u4 CLI entry point
├── setup.sh                      — one-time setup (deps + uv tool install)
├── README.md
├── config.example.json           — template
├── docs/                         — detail docs (configuration, usage, troubleshooting, this file)
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
│   ├── test_per_day_sync.py      — filter + tracking log + week derivation/shift (10 tests)
│   ├── test_mapping_resolver.py  — resolver pipeline (7 tests)
│   ├── test_ask_for_arbauft.py   — interactive prompt (7 tests)
│   └── test_jira_connection.py   — manual Jira/Tempo smoke (live)
└── tools/
    ├── inspect_ui.py             — dump Unit4 element attributes
    └── debug_dialog_inputs.py    — dump dialog input IDs
```

## Local venv vs. global tool

There are **two** Python environments after setup:

- The **local `.venv`** (created by `uv sync`) is for development —
  running the test suite (`uv run pytest`) or the diagnostic tools
  (`uv run python tools/inspect_ui.py`).
- The **global `j2u4` command** lives in its own isolated uv-tool venv
  (`~/.local/share/uv/tools/j2u4/`). It works from any working directory
  and is independent of the local `.venv`.

The two do not interfere. Updating the repo + running `./setup.sh`
keeps both in sync.

## Updating

After pulling new commits from the repo:

```bash
cd j2u4
git pull
./setup.sh --upgrade
```

`--upgrade` refreshes deps + Chromium + the global `j2u4` binary, but
skips the config/mapping bootstrap (those are already present after the
first run). Without the flag, `./setup.sh` does the full install — also
idempotent, but a few extra "already exists" lines.

If you only want to refresh the binary without re-running every step:

```bash
uv tool install --from . j2u4 --reinstall
```

## Testing

Since Unit4 is a live enterprise system with no sandbox or staging
environment, full end-to-end tests are not feasible. The test suite
therefore focuses on what **can** be verified offline:

```bash
# Install dev dependencies (once)
uv sync --extra dev

# Run tests
uv run pytest
```

**What is tested:**
- Regex patterns (day labels, worklog markers, ticket keys, ArbAuft
  codes) against both German and English inputs
- Locale configuration consistency (both locales define the same keys,
  non-empty values)
- Mapping resolver pipeline (regex → file → conflict → none)
- Interactive ArbAuft prompt (regex/file/conflict/no-match paths)
- Per-day filter + tracking log behaviour
- Failure capture helper

**What requires manual verification:**
- Browser automation against a live Unit4 instance (`j2u4 --check`,
  then `j2u4 --day YYYY-MM-DD --execute`)
- Session handling, login flow, 2FA

## Security

- **Never commit** `config.json` or `session.json`
- These files are in `.gitignore`
- Use `config.example.json` as template
