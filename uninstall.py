#!/usr/bin/env python3
"""Uninstall the Claude Code status line.

Removes the statusLine key from settings.json and deletes
~/.claude/statusline.py (or the $CLAUDE_CONFIG_DIR equivalents). Any
timestamped settings.json.bak.* backups are left in place in case you want
to restore anything else by hand.
"""

import json
import os
import sys
from pathlib import Path


def claude_config_dir() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".claude"


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    config = claude_config_dir()
    settings_path = config / "settings.json"
    script_path = config / "statusline.py"
    cache_path = config / "statusline-cache.json"

    removed_key = False
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text("utf-8"))
        except json.JSONDecodeError as e:
            fail(f"{settings_path} is not valid JSON ({e}). Fix by hand, then re-run.")
        if isinstance(settings, dict) and "statusLine" in settings:
            del settings["statusLine"]
            tmp = settings_path.with_suffix(".json.tmp")
            try:
                tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
                tmp.replace(settings_path)
            except Exception as e:
                fail(f"could not write {settings_path}: {e}")
            removed_key = True

    removed_script = False
    if script_path.exists():
        try:
            script_path.unlink()
            removed_script = True
        except Exception as e:
            fail(f"could not delete {script_path}: {e}")

    if cache_path.exists():
        try:
            cache_path.unlink()
        except Exception:
            pass
    # Sweep any orphaned atomic-write temp files (statusline-cache.<pid>.tmp)
    for stale in config.glob("statusline-cache.*.tmp"):
        try:
            stale.unlink()
        except Exception:
            pass

    if not removed_key and not removed_script:
        print("Nothing to uninstall — status line was not installed.")
        return 0

    print("Claude Code status line uninstalled.")
    if removed_key:
        print(f"  removed statusLine key from {settings_path}")
    if removed_script:
        print(f"  deleted {script_path}")
    backups = sorted(config.glob("settings.json.bak*"))
    if backups:
        print(f"  settings backups kept: {len(backups)} (latest: {backups[-1]})")
    print()
    print("Restart Claude Code, or type /hooks to reload the config.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
