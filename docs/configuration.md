# Configuration

## Interactive setup (`j2u4 --init`)

The fastest way to create or refresh `config.json` is the interactive
prompt:

```bash
j2u4 --init
```

It walks through the four sections (Jira / Tempo / Unit4 / mapping
help URLs), prints the helper URL where each token is created, hides
input for tokens, and reuses any existing values as defaults — pressing
ENTER on a token keeps the current one. A summary with masked tokens
is shown before anything is written; you confirm with `y` to save.

The file lands in the user-config directory by default
(`~/.config/j2u4/config.json` on Linux/macOS, `%APPDATA%\j2u4\config.json`
on Windows). Override the location with `$J2U4_CONFIG_DIR`.

## API tokens

If you fill in `config.json` by hand instead of `--init`:

- **Jira API Token**: [Create here](https://id.atlassian.com/manage-profile/security/api-tokens)
- **Tempo API Token**: Go to Tempo > Settings > API Integration
  `https://<YOUR-ORG>.atlassian.net/plugins/servlet/ac/io.tempo.jira/tempo-app#!/configuration/api-integration`

### Required token permissions

Both tokens are **read-only** — j2u4 never writes to Jira or Tempo.

**Jira** — the Atlassian API token has no scope picker; it inherits your
account's Jira permissions. You need:

- **Browse Projects** on every Jira project whose tickets you book worklogs against

**Tempo** — the token has explicit scopes when you create it. Tick exactly:

- **View worklogs** — needed for `/4/worklogs/*` endpoints
- **View accounts** — needed for `/4/accounts` (the Tempo account/cost-center list j2u4 uses to map worklogs)

> Watch out: **`View accounts` is not the same scope as `View projects`.**
> Tempo "accounts" are cost-centers, "projects" are Jira projects. A token
> with only `View worklogs` + `View projects` will fail the sync with
> `403 Access denied` at the account-listing step.

All other scopes (`Manage worklogs`, `Manage accounts`, plans, teams, …)
can stay disabled. Verify the token setup with `j2u4 --check`, which
tests both Tempo scopes separately and tells you which one is missing.

## `config.json` — full structure

Only the `jira`, `tempo`, and `unit4` sections are required. Everything
else has sensible defaults — leave it out unless you want to override.

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
  },
  "mapping": {
    "help_urls": [
      "https://<YOUR-ORG>.atlassian.net/wiki/spaces/<SPACE>/pages/.../Customer+Projects",
      "https://<YOUR-ORG>.atlassian.net/wiki/spaces/<SPACE>/pages/.../Internal+Projects"
    ]
  },
  "debug": {
    "capture_enabled": true,
    "capture_cap": 10,
    "capture_video": true
  }
}
```

## Field reference

| Path | Required | Default | What it does |
|---|---|---|---|
| `jira.base_url` | yes | — | Atlassian Cloud URL, e.g. `https://acme.atlassian.net` |
| `jira.user_email` | yes | — | Your Atlassian account email |
| `jira.api_token` | yes | — | Jira API token (Atlassian account → API tokens) |
| `tempo.api_token` | yes | — | Tempo Cloud API token (Tempo settings → API integration) |
| `unit4.url` | yes | — | The Unit4 ERP entry URL for your tenant |
| `mapping.help_urls` | optional | `[]` | URLs shown in the interactive prompt when an account cannot be resolved automatically — e.g. your team's Confluence pages listing the workorder catalogue. The list is **read** by the prompt, never hardcoded in the code |
| `debug.capture_enabled` | optional | `true` | Capture a Playwright trace per failed browser operation (set `--no-capture` on the CLI to override per run) |
| `debug.capture_dir` | optional | OS temp dir (`/tmp/j2u4-captures` on Linux/macOS, `%TEMP%\j2u4-captures` on Windows) | Where capture folders land. Default points at temp so traces don't pile up in your working directory; the OS cleans them eventually. Override to a stable path if you want to keep traces around |
| `debug.capture_cap` | optional | `10` | Max trace folders kept per run, prevents disk floods on cascading failures |
| `debug.capture_video` | optional | `true` | Record a `.webm` of the browser session alongside the trace (`--no-video` to override) |

## Environment variables

| Variable | Effect |
|---|---|
| `J2U4_CONFIG_DIR` | Override the directory where `config.json`, `mapping.json`, `session.json`, and `sync_history.log` are read/written. Top of the lookup chain (see below) |
| `J2U4_CAPTURE_ALL` | If set to `1`, every trace chunk is persisted (not only failures) and the per-run cap is disabled. Useful for one-shot performance / behaviour analysis |

## Where j2u4 looks for `config.json`

The same directory holds `config.json`, `mapping.json`, `session.json`,
and `sync_history.log`. Lookup order:

1. `$J2U4_CONFIG_DIR` if set (explicit override)
2. The current working directory if it contains a `config.json` (the
   "I'm in the repo, use the local files" case for development)
3. The OS-conventional user-config dir:
   - Linux/macOS: `~/.config/j2u4/` (XDG-friendly, honours `$XDG_CONFIG_HOME`)
   - Windows: `%APPDATA%\j2u4\`

`./setup.sh` writes the config there on the first run, so `j2u4` works
from any working directory afterwards.

Failure-capture folders go to a **temp directory** by default
(`/tmp/j2u4-captures` on Linux/macOS, `%TEMP%\j2u4-captures` on Windows)
so they don't pile up in your working directory and the OS cleans them
eventually. Override via `debug.capture_dir` if you want them somewhere
stable.

## First run (login)

On first run, Unit4 will prompt for login (2FA). The session is saved to
`session.json` (in the same directory as `config.json`) for subsequent runs.
