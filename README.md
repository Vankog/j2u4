# j2u4

Sync Tempo worklogs to Unit4 Zeiterfassung via Playwright browser automation.

## Disclaimer

**USE AT YOUR OWN RISK.** No warranty, no support, no guarantees. Always
verify your time entries manually in Unit4 after syncing. Full disclaimer
in [`LICENSE`](LICENSE) and below.

### Tested platforms

- **Tested**: Linux + German Unit4 UI (DD/MM dates, comma decimals; the
  browser context is hardcoded to `locale='de'` because Unit4 silently
  strips period decimals).
- **NOT tested**: Windows (use WSL), and English Unit4 UI / English
  locale formats. The codebase has bilingual selectors but no live
  verification against an EN tenant yet.

CDDS AB and the contributors make no guarantees about correctness or
suitability, accept no liability, and provide no support obligations.
This tool automates browser interactions with Unit4, which may break
at any time due to UI changes.

## Quickstart

**1. Install uv** (Python comes with it):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**2. Install j2u4**:

```bash
git clone https://github.com/cdds-ab/j2u4.git
cd j2u4
./setup.sh
```

**3. Enter your tokens** (Jira / Tempo / Unit4 — interactive prompts):

```bash
j2u4 --init
```

**4. Sync today**:

```bash
j2u4 --check                       # test connections
j2u4 --day $(date -I)              # preview (no changes)
j2u4 --day $(date -I) --execute    # actually sync
```

> **Windows users:** run `wsl --install` in an Administrator
> PowerShell first, then execute the four steps inside the Ubuntu
> terminal.

## Updating

```bash
cd j2u4 && git pull && ./setup.sh --upgrade
```

`--upgrade` refreshes deps + Chromium + the global `j2u4` binary while
leaving your config and mapping files alone.

## Documentation

| Topic | Where |
|---|---|
| Config file layout, fields, env vars, lookup paths | [docs/configuration.md](docs/configuration.md) |
| CLI reference, sync semantics, mapping resolver, tracking log | [docs/usage.md](docs/usage.md) |
| Common errors, failure captures, trace viewer | [docs/troubleshooting.md](docs/troubleshooting.md) |
| Repo layout, testing, dev environment | [docs/development.md](docs/development.md) |

## License

MIT — see [LICENSE](LICENSE).
