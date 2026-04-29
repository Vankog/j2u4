#!/bin/bash
# Setup script for j2u4.
#
# Usage:
#   ./setup.sh             — full setup (deps, Chromium, config, mapping, j2u4 install)
#   ./setup.sh --upgrade   — refresh deps + j2u4 binary only (skip config/mapping bootstrap)
#   ./setup.sh --help      — show this message

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

MODE="install"
for arg in "$@"; do
    case "$arg" in
        --upgrade)
            MODE="upgrade"
            ;;
        --help|-h)
            sed -n '2,7p' "$0" | sed 's/^# //;s/^#//'
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

if ! command -v uv &> /dev/null; then
    echo "    [x] uv NOT FOUND"
    echo
    echo "[!] ERROR: uv is required but not installed."
    echo
    echo "    Install uv:"
    echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo
    echo "    More info: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

echo "    [ok] uv $(uv --version 2>/dev/null | awk '{print $2}')"
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
    # Create config from template if needed
    if [ ! -f "config.json" ]; then
        if [ -f "config.example.json" ]; then
            echo "[3] Creating config.json from template..."
            cp config.example.json config.json
            CONFIG_CREATED=true
        else
            echo "[3] No config.example.json found, skipping config creation"
            CONFIG_CREATED=false
        fi
    else
        echo "[3] config.json already exists"
        CONFIG_CREATED=false
    fi

    # Migrate legacy mapping filename if needed
    if [ ! -f "mapping.json" ] && [ -f "account_to_arbauft_mapping.json" ]; then
        echo "[4] Renaming legacy account_to_arbauft_mapping.json -> mapping.json"
        mv account_to_arbauft_mapping.json mapping.json
    fi

    # Create empty mapping file if needed
    if [ ! -f "mapping.json" ]; then
        echo "[4] Creating empty mapping file..."
        echo "{}" > mapping.json
        MAPPING_CREATED=true
    else
        MAPPING_COUNT=$(grep -c "unit4_arbauft" mapping.json 2>/dev/null || echo "0")
        echo "[4] Mapping file exists ($MAPPING_COUNT mappings)"
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
    if [ "$CONFIG_CREATED" = true ]; then
        echo "  1. Edit config.json with your API tokens:"
        echo "     - Jira: https://id.atlassian.com/manage-profile/security/api-tokens"
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
