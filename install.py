#!/usr/bin/env python3
"""Install the Claude Code status line.

Copies statusline.py into ~/.claude/ (or $CLAUDE_CONFIG_DIR if set) and adds
a statusLine entry to settings.json, backing up the original first.

Safe to re-run — idempotent.
"""

import json
import os
import shutil
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
    here = Path(__file__).resolve().parent
    src = here / "statusline.py"
    if not src.exists():
        fail(f"statusline.py not found next to the installer (looked at {src})")

    config = claude_config_dir()
    if not config.exists():
        fail(
            f"Claude Code config directory not found at {config}\n"
            "Install Claude Code first, then re-run this script.\n"
            "Or set CLAUDE_CONFIG_DIR to override the location."
        )

    dest_script = config / "statusline.py"
    settings_path = config / "settings.json"
    backup_path = config / "settings.json.bak"

    # 1. Copy statusline.py into the config dir
    try:
        shutil.copyfile(src, dest_script)
    except Exception as e:
        fail(f"could not copy statusline.py to {dest_script}: {e}")

    # 2. Read existing settings.json (or start fresh)
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text("utf-8"))
        except json.JSONDecodeError as e:
            fail(
                f"{settings_path} is not valid JSON ({e}). "
                "Fix it by hand, then re-run this script."
            )
        if not isinstance(settings, dict):
            fail(f"{settings_path} does not contain a JSON object at the top level")
    else:
        settings = {}

    # 3. Back up before modifying
    try:
        backup_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except Exception as e:
        fail(f"could not write backup to {backup_path}: {e}")

    # 4. Merge in the statusLine key. Quote both paths and use forward slashes
    # so the command works on Windows (git-bash), macOS, and Linux alike.
    exe = Path(sys.executable).as_posix()
    command = f'"{exe}" "{dest_script.as_posix()}"'
    settings["statusLine"] = {
        "type": "command",
        "command": command,
        "padding": 0,
    }

    # 5. Write settings.json atomically
    tmp = settings_path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        tmp.replace(settings_path)
    except Exception as e:
        fail(f"could not write {settings_path}: {e}")

    print("Claude Code status line installed.")
    print(f"  script:   {dest_script}")
    print(f"  settings: {settings_path}")
    print(f"  backup:   {backup_path}")
    print()
    print("Restart Claude Code, or type /hooks to reload the config.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
