#!/bin/bash
# Setup script for j2u4.
#
# Usage:
#   ./setup.sh             — full setup (deps, Chromium, config, mapping, j2u4 install)
#   ./setup.sh --upgrade   — refresh deps + j2u4 binary only (skip config/mapping bootstrap)
#   ./setup.sh --no-deps   — skip the Linux system-library install for Chromium
#                            (use when you already have libatk/libnss3/... or aren't on apt)
#   ./setup.sh --help      — show this message

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

MODE="install"
SKIP_DEPS=false
for arg in "$@"; do
    case "$arg" in
        --upgrade)
            MODE="upgrade"
            ;;
        --no-deps)
            SKIP_DEPS=true
            ;;
        --help|-h)
            sed -n '2,9p' "$0" | sed 's/^# //;s/^#//'
            exit 0
            ;;
        *)
            echo "[!] Unknown argument: $arg"
            echo "    Run ./setup.sh --help for options."
            exit 2
            ;;
    esac
done

echo "========================================"
if [ "$MODE" = "upgrade" ]; then
    echo "j2u4 Upgrade"
else
    echo "j2u4 Setup"
fi
echo "========================================"
echo

# Check prerequisites
echo "[0] Checking prerequisites..."
echo "    j2u4 needs only one tool installed up front: uv."
echo "    Python is managed by uv automatically — no separate install."
echo

if ! command -v uv &> /dev/null; then
    echo "    [x] uv NOT FOUND"
    echo
    echo "[!] ERROR: uv is required but not installed."
    echo
    echo "    Install uv (one-liner):"
    echo "      curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo
    echo "    The installer drops 'uv' in ~/.local/bin and updates your shell"
    echo "    rc file. Open a new terminal (or 'source ~/.bashrc' / 'source ~/.zshrc')"
    echo "    so PATH picks it up, then re-run ./setup.sh."
    echo
    echo "    More info: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

UV_VERSION="$(uv --version 2>/dev/null | awk '{print $2}')"
PY_VERSION="$(uv python find 2>/dev/null | xargs -r -I{} sh -c '{} --version 2>/dev/null' | awk '{print $2}')"
echo "    [ok] uv $UV_VERSION"
if [ -n "$PY_VERSION" ]; then
    echo "    [ok] Python $PY_VERSION (managed by uv)"
else
    echo "    [ok] Python: uv will fetch one when needed"
fi
echo

# Install/refresh dependencies (uv handles Python + venv automatically)
echo "[1] Installing/refreshing dependencies..."
uv sync

# Install/refresh Playwright Chromium
echo "[2] Installing/refreshing Chromium for Playwright..."
uv run playwright install chromium

# Bootstrap-only steps: config and mapping files. Skipped on --upgrade
# because we assume those are already present and edited.
if [ "$MODE" = "install" ]; then
    # Decide where config + mapping live. Priority:
    #   1. $J2U4_CONFIG_DIR (explicit override)
    #   2. ./config.json in cwd (Repo-style, unchanged)
    #   3. user config dir: ~/.config/j2u4 (Linux/macOS) or %APPDATA%/j2u4
    if [ -n "$J2U4_CONFIG_DIR" ]; then
        TARGET_DIR="$J2U4_CONFIG_DIR"
    elif [ -f "config.json" ]; then
        TARGET_DIR="."
    else
        # Default to OS user-config dir; XDG-friendly on Linux/macOS.
        TARGET_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/j2u4"
    fi
    mkdir -p "$TARGET_DIR"

    # Create config from template if needed
    if [ ! -f "$TARGET_DIR/config.json" ]; then
        if [ -f "config.example.json" ]; then
            echo "[3] Creating $TARGET_DIR/config.json from template..."
            cp config.example.json "$TARGET_DIR/config.json"
            CONFIG_CREATED=true
            CONFIG_PATH="$TARGET_DIR/config.json"
        else
            echo "[3] No config.example.json found, skipping config creation"
            CONFIG_CREATED=false
            CONFIG_PATH=""
        fi
    else
        echo "[3] config.json already exists at $TARGET_DIR/"
        CONFIG_CREATED=false
        CONFIG_PATH="$TARGET_DIR/config.json"
    fi

    # Migrate legacy mapping filename if needed (in TARGET_DIR)
    if [ ! -f "$TARGET_DIR/mapping.json" ] && [ -f "$TARGET_DIR/account_to_arbauft_mapping.json" ]; then
        echo "[4] Renaming legacy account_to_arbauft_mapping.json -> mapping.json (in $TARGET_DIR)"
        mv "$TARGET_DIR/account_to_arbauft_mapping.json" "$TARGET_DIR/mapping.json"
    fi
    # Also migrate from cwd if the user previously had it there
    if [ ! -f "$TARGET_DIR/mapping.json" ] && [ -f "account_to_arbauft_mapping.json" ] && [ "$TARGET_DIR" != "." ]; then
        echo "[4] Moving legacy ./account_to_arbauft_mapping.json -> $TARGET_DIR/mapping.json"
        mv account_to_arbauft_mapping.json "$TARGET_DIR/mapping.json"
    fi
    if [ ! -f "$TARGET_DIR/mapping.json" ] && [ -f "mapping.json" ] && [ "$TARGET_DIR" != "." ]; then
        echo "[4] Moving ./mapping.json -> $TARGET_DIR/mapping.json"
        mv mapping.json "$TARGET_DIR/mapping.json"
    fi

    # Create empty mapping file if needed
    if [ ! -f "$TARGET_DIR/mapping.json" ]; then
        echo "[4] Creating empty $TARGET_DIR/mapping.json..."
        echo "{}" > "$TARGET_DIR/mapping.json"
        MAPPING_CREATED=true
    else
        MAPPING_COUNT=$(grep -c "unit4_arbauft" "$TARGET_DIR/mapping.json" 2>/dev/null || echo "0")
        echo "[4] Mapping file exists at $TARGET_DIR/ ($MAPPING_COUNT mappings)"
        MAPPING_CREATED=false
    fi
else
    # On upgrade, still migrate the legacy filename if found — cheap, harmless.
    if [ ! -f "mapping.json" ] && [ -f "account_to_arbauft_mapping.json" ]; then
        echo "[3] Renaming legacy account_to_arbauft_mapping.json -> mapping.json"
        mv account_to_arbauft_mapping.json mapping.json
    fi
    CONFIG_CREATED=false
    MAPPING_CREATED=false
fi

echo
# Install/refresh the global tool so `j2u4` works from anywhere
echo "[5] Installing/refreshing global j2u4 command via uv..."
uv tool install --from . j2u4 --reinstall

# Ensure the uv-tool venv has Chromium too. Playwright caches binaries
# under ~/.cache/ms-playwright keyed by Playwright version; the local
# .venv and the uv-tool venv may resolve to different Playwright
# versions, so install for the tool venv directly via its own
# `playwright` binary (avoids the `uv tool run --from j2u4 playwright`
# warning about playwright not being a j2u4-owned executable).
echo "[5b] Installing Chromium for the j2u4 tool venv..."
TOOL_PLAYWRIGHT="$(uv tool dir 2>/dev/null)/j2u4/bin/playwright"
if [ -x "$TOOL_PLAYWRIGHT" ]; then
    "$TOOL_PLAYWRIGHT" install chromium
else
    # Fallback for older uv layouts
    uv tool run --from j2u4 playwright install chromium
fi

# Step [6] System libraries for Chromium (Linux only). chrome-headless-shell
# needs libatk-1.0, libnss3, libxkbcommon0, ... — not bundled with the
# Playwright download. Skipped on macOS (deps come with Chromium) and
# Windows (this script only runs under bash anyway).
if [[ "$OSTYPE" == "linux-gnu"* ]] && [ "$SKIP_DEPS" != true ]; then
    echo
    echo "[6] Installing Chromium system libraries (apt)..."
    echo "    Without these, the first sync crashes with TargetClosedError."
    echo "    Pass --no-deps to skip (you'll need libatk1.0-0 libnss3 ... yourself)."
    DEPS_PLAYWRIGHT="$TOOL_PLAYWRIGHT"
    if [ ! -x "$DEPS_PLAYWRIGHT" ]; then
        # Fall back to the local .venv playwright if the tool one isn't there
        DEPS_PLAYWRIGHT="$(command -v playwright || true)"
    fi
    if [ -z "$DEPS_PLAYWRIGHT" ]; then
        echo "    [skip] no playwright binary found — install system libs manually:"
        echo "      sudo apt-get install -y libatk1.0-0 libatk-bridge2.0-0 libnss3 \\"
        echo "        libnspr4 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \\"
        echo "        libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2"
    elif [ "$(id -u)" = "0" ]; then
        # Already root — no sudo prefix needed (e.g. Docker, CI containers)
        "$DEPS_PLAYWRIGHT" install-deps chromium || {
            echo "    [!] install-deps failed. Install the libs manually (see above) and re-run."
        }
    elif command -v sudo &> /dev/null; then
        sudo "$DEPS_PLAYWRIGHT" install-deps chromium || {
            echo "    [!] install-deps failed. j2u4 will install, but the first sync will"
            echo "        crash with TargetClosedError until you install the libs manually."
        }
    else
        echo "    [skip] sudo not found and not running as root."
        echo "    Run as root: $DEPS_PLAYWRIGHT install-deps chromium"
    fi
fi

echo
echo "========================================"
if [ "$MODE" = "upgrade" ]; then
    echo "Upgrade complete!"
else
    echo "Setup complete!"
fi
echo "========================================"
echo

if [ "$MODE" = "install" ]; then
    echo "Next steps:"
    if [ "$CONFIG_CREATED" = true ] && [ -n "$CONFIG_PATH" ]; then
        echo "  1. Edit your config with API tokens:"
        echo "       \$EDITOR $CONFIG_PATH"
        echo "     - Jira:  https://id.atlassian.com/manage-profile/security/api-tokens"
        echo "     - Tempo: Settings > API Integration in Tempo"
        echo
    fi
    echo "  2. Test connectivity:"
    echo "     j2u4 --check"
    echo
    echo "  3. Sync today's time entries:"
    echo "     j2u4 --day \$(date -I)             # dry-run first"
    echo "     j2u4 --day \$(date -I) --execute   # actually sync"
else
    echo "Verify with:"
    echo "  j2u4 --check"
fi
echo
