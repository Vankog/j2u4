# Troubleshooting

## Common errors

### "config.json not found"
- Run `./setup.sh` to create the skeleton, then `j2u4 --init` to fill it in
- Or copy manually: `cp config.example.json ~/.config/j2u4/config.json`

### "Authentication failed" / API errors
- Run `j2u4 --check` to diagnose connectivity issues
- Verify your API tokens are correct in `config.json`
- Jira token: Check it's not expired at [Atlassian Account](https://id.atlassian.com/manage-profile/security/api-tokens)
- Tempo token: Regenerate in Tempo Settings > API Integration

### "Cannot connect to Unit4"
- Make sure you're connected to VPN (if required)
- Check the URL in `config.json` is correct

### `TargetClosedError: BrowserType.launch` on Linux
- Chromium (chrome-headless-shell) can't start because system libs are missing
  (typically `libatk-1.0.so.0`, `libnss3`, `libxkbcommon0`, …). Verify with:
  `<playwright-cache>/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell --version`
  — if it answers `error while loading shared libraries: …`, that's the cause.
- Fix: re-run `./setup.sh` (step `[6]` installs the libs via apt), or do it
  manually: `sudo "$(uv tool dir)/j2u4/bin/playwright" install-deps chromium`
- On non-Debian/Ubuntu distros `playwright install-deps` won't help — install
  the libs through your package manager directly.

### "Page not loaded" / Add button not found
- The script waits for the page to load, but Unit4 can be slow
- Try `--slow 2` (or higher) to give Unit4 more breathing room
- If it still times out, try running again

### Duplicate entries
- The script deletes all `[WL:xxx]` entries before creating new ones
- If duplicates appear, run the script again to clean them up

### Session expired
- The script will detect this and prompt for re-login
- If issues persist, delete `session.json` and run again

### A specific day failed

Each invocation handles exactly one day, so failures are isolated. When
a day fails:

1. Check the captures directory — the trace is there. Default is
   `/tmp/j2u4-captures/RUN_*/` (Linux/macOS) or
   `%TEMP%\j2u4-captures\RUN_*\` (Windows); see your `debug.capture_dir`
   for overrides.
2. Check `sync_history.log` — the most recent block records `SAVE fail`
   with a back-reference to the exact capture folder.
3. Open the trace: `uv run playwright show-trace <path-from-log>/trace.zip`
4. Fix the underlying cause and re-run `j2u4 --day YYYY-MM-DD --execute`.

The re-run will detect the partial state (some `[WL:]` markers from the
failed run, some missing) and bring the day to consistency by deleting
and recreating the target day's markers.

### Unit4 language

- The browser automation works with both **German** and **English**
  Unit4 UI in principle (the German path is the only one verified live).
- Most selectors use stable element IDs; remaining text-based selectors
  try both languages automatically.
- If you encounter issues with a different UI language, run the UI
  inspector and share the output:
  ```bash
  uv run python tools/inspect_ui.py
  ```
  This opens Unit4, scans all UI elements, and saves their HTML
  attributes to `ui_inspection.json`.

## Failure captures

When something goes wrong during the browser automation (Add button
missing, dialog freezes, hours fill fails, save errors out), the script
writes a **Playwright trace** for that specific failure. Each capture is
a self-contained folder you can ZIP up and send for diagnosis.

### Where captures land

By default the captures live under the OS temp directory:
- Linux/macOS: `/tmp/j2u4-captures/RUN_<ts>/`
- Windows: `%TEMP%\j2u4-captures\RUN_<ts>\`

```
<capture_dir>/
└── RUN_2026-04-28T14-23-05/
    ├── 2026-04-28T14-23-49_CREATE_PROJ-123/
    │   ├── trace.zip       # Playwright trace — open in Trace Viewer
    │   ├── context.json    # worklog data, step, exception, recent page errors
    │   └── README.txt      # short instructions
    └── *.webm              # browser video for the whole run (if capture_video)
```

If the run finishes without any failure, the whole `RUN_*` folder is
deleted automatically — no clutter from successful runs. The OS will
eventually clean up leftover folders too.

Override `debug.capture_dir` in `config.json` to keep captures in a
stable location.

### Opening a trace

```bash
uv run playwright show-trace /tmp/j2u4-captures/RUN_<ts>/<failure-folder>/trace.zip
```

The Trace Viewer shows every action with before/after DOM snapshots,
network and console logs, and a screenshot timeline. This is usually
enough to see *exactly* what the browser was doing when the failure
happened.

### Privacy warning

Traces and videos contain **DOM content of the entire week** that was
visible during the failure — not only the failing worklog. Other
tickets, descriptions, customer names may be present. Review captures
before sharing externally.

### Disabling captures

Set in `config.json`:

```json
"debug": {
  "capture_enabled": false
}
```

The whole `debug` block is optional. Defaults: `capture_enabled: true`,
`capture_dir` = OS temp dir,
`capture_cap: 10`, `capture_video: true`.
