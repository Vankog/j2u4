"""Utility functions for Tempo to Unit4 sync."""

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

# Filenames (resolved relative to a directory chosen at runtime)
SESSION_FILENAME = "session.json"
CONFIG_FILENAME = "config.json"
MAPPING_FILENAME = "mapping.json"
LEGACY_MAPPING_FILENAME = "account_to_arbauft_mapping.json"
SYNC_HISTORY_FILENAME = "sync_history.log"


def user_config_dir() -> Path:
    """Return the j2u4 user-config directory using OS conventions.

    Linux / macOS: $XDG_CONFIG_HOME/j2u4 (default ~/.config/j2u4)
    Windows:       %APPDATA%/j2u4
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "j2u4"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "j2u4"


def _resolve_dir() -> Path:
    """Pick the directory to search for config / mapping / session.

    Order:
      1. J2U4_CONFIG_DIR environment variable (explicit override)
      2. Current working directory, if it already contains config.json
         (preserves repo-style invocations: `cd repo && j2u4 …`)
      3. user_config_dir() — the OS-conventional location for installed CLI
    """
    env = os.environ.get("J2U4_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    if Path(CONFIG_FILENAME).exists():
        return Path(".")
    return user_config_dir()


def config_path() -> Path:
    return _resolve_dir() / CONFIG_FILENAME


def mapping_path() -> Path:
    return _resolve_dir() / MAPPING_FILENAME


def legacy_mapping_path() -> Path:
    return _resolve_dir() / LEGACY_MAPPING_FILENAME


def session_path() -> Path:
    return _resolve_dir() / SESSION_FILENAME


def sync_history_path() -> Path:
    return _resolve_dir() / SYNC_HISTORY_FILENAME


def load_config() -> dict:
    """Load config.json with Jira and Tempo credentials."""
    with open(config_path()) as f:
        return json.load(f)


def validate_config(config: dict) -> list[str]:
    """Validate config structure and return list of error messages.

    Returns:
        Empty list if valid, otherwise list of error messages.
    """
    errors = []

    # Check required sections
    for section in ["jira", "tempo", "unit4"]:
        if section not in config:
            errors.append(f"Missing section '{section}' in config.json")

    # Check Jira credentials
    if "jira" in config:
        for key in ["base_url", "user_email", "api_token"]:
            if not config["jira"].get(key):
                errors.append(f"Missing jira.{key}")

    # Check Tempo
    if "tempo" in config:
        if not config["tempo"].get("api_token"):
            errors.append("Missing tempo.api_token")

    # Check Unit4
    if "unit4" in config:
        if not config["unit4"].get("url"):
            errors.append("Missing unit4.url")

    return errors


def load_config_safe() -> dict | None:
    """Load config with user-friendly error messages.

    Returns:
        Config dict if valid, None if errors occurred.
    """
    cfg = config_path()
    if not cfg.exists():
        print(f"[!] ERROR: config.json not found at {cfg}")
        print()
        print("    j2u4 looked in this order:")
        print("      1. $J2U4_CONFIG_DIR (if set)")
        print("      2. ./config.json in the current directory")
        print(f"      3. {user_config_dir()}/config.json")
        print()
        print("    Create one of these from the template, e.g.:")
        print(f"      mkdir -p {user_config_dir()}")
        print(f"      cp config.example.json {user_config_dir()}/config.json")
        print(f"      $EDITOR {user_config_dir()}/config.json")
        print()
        return None

    try:
        config = load_config()
    except json.JSONDecodeError as e:
        print("[!] ERROR: config.json is not valid JSON!")
        print(f"    Line {e.lineno}, column {e.colno}: {e.msg}")
        print()
        print("    Check for missing commas, quotes, or brackets.")
        return None

    errors = validate_config(config)
    if errors:
        print("[!] ERROR: config.json is incomplete:")
        for err in errors:
            print(f"    - {err}")
        print()
        print("    See config.example.json for the required structure.")
        return None

    return config


def load_mapping() -> dict:
    """Load account-to-arbauft mapping.

    One-shot migration: if the legacy filename exists but the new one
    does not, rename it. After the next save_mapping(), only the new
    name persists.
    """
    mp = mapping_path()
    legacy = legacy_mapping_path()
    if not mp.exists() and legacy.exists():
        try:
            legacy.rename(mp)
            print(f"[*] Renamed legacy {legacy} -> {mp}")
        except OSError as e:
            # If rename fails (permissions, cross-device, etc.), fall through
            # and read from the legacy path so the run can still proceed.
            print(f"[!] Could not rename legacy mapping file: {e}")
            with open(legacy) as f:
                return json.load(f)

    if mp.exists():
        with open(mp) as f:
            return json.load(f)
    return {}


def save_mapping(mapping: dict) -> None:
    """Save account-to-arbauft mapping."""
    mp = mapping_path()
    mp.parent.mkdir(parents=True, exist_ok=True)
    with open(mp, "w") as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)


def get_week_dates(week_str: str) -> tuple[str, str]:
    """Get start and end date (Mon-Sun) for a week string YYYYWW."""
    year = int(week_str[:4])
    week = int(week_str[4:])
    # ISO week: Jan 4 is always in week 1
    jan4 = datetime(year, 1, 4)
    start_of_week1 = jan4 - timedelta(days=jan4.weekday())
    week_start = start_of_week1 + timedelta(weeks=week - 1)
    week_end = week_start + timedelta(days=6)
    return week_start.strftime("%Y-%m-%d"), week_end.strftime("%Y-%m-%d")


def get_current_week() -> str:
    """Get current week as YYYYWW."""
    return datetime.now().strftime("%G%V")


async def retry_async(
    operation: Callable[[], T],
    max_attempts: int = 3,
    delay: float = 1.0,
    on_retry: Callable[[int, Exception], None] | None = None,
) -> T | None:
    """Execute an async operation with retries.

    Args:
        operation: Async callable to execute
        max_attempts: Maximum number of attempts
        delay: Delay in seconds between attempts
        on_retry: Optional callback when retrying (attempt_num, exception)

    Returns:
        Result of operation or None if all attempts failed
    """
    last_error = None
    for attempt in range(max_attempts):
        try:
            return await operation()
        except Exception as e:
            last_error = e
            if attempt < max_attempts - 1:
                if on_retry:
                    on_retry(attempt + 1, e)
                await asyncio.sleep(delay)
    return None
