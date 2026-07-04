#!/usr/bin/env python3
"""Install the Claude Code status line.

Copies statusline.py into ~/.claude/ (or $CLAUDE_CONFIG_DIR if set) and adds
a statusLine entry to settings.json, first backing settings.json up to a
timestamped settings.json.bak.<stamp> copy.

Safe to re-run — idempotent, and each run makes a fresh backup rather than
overwriting an earlier one.
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path

# statusline.py uses 3.9+ syntax (str.removeprefix, PEP 585 annotations
# behind `from __future__ import annotations`). The interpreter running this
# installer is the one that gets pinned into settings.json, so gate here.
MIN_PYTHON = (3, 9)


def claude_config_dir() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".claude"


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    if sys.version_info < MIN_PYTHON:
        fail(
            f"Python {'.'.join(map(str, MIN_PYTHON))}+ is required "
            f"(you're running {sys.version.split()[0]} at {sys.executable}).\n"
            "Any Python 3.9 or newer works — macOS's system /usr/bin/python3 "
            "(3.9.6) is enough — or install one with `brew install python` "
            "and re-run the installer with it."
        )

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

    # 3. Back up before modifying — a byte-for-byte copy with a timestamp, so
    # re-running the installer never clobbers an earlier backup.
    backup_path = None
    if settings_path.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup_path = config / f"settings.json.bak.{stamp}"
        n = 1
        while backup_path.exists():  # two runs in the same second
            backup_path = config / f"settings.json.bak.{stamp}-{n}"
            n += 1
        try:
            shutil.copyfile(settings_path, backup_path)
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
        tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        tmp.replace(settings_path)
    except Exception as e:
        fail(f"could not write {settings_path}: {e}")

    print("Claude Code status line installed.")
    print(f"  script:   {dest_script}")
    print(f"  settings: {settings_path}")
    if backup_path is not None:
        print(f"  backup:   {backup_path}")
    if sys.prefix != sys.base_prefix:
        print()
        print(
            f"note: {exe} is a virtualenv interpreter. The status line will\n"
            "break if this venv is deleted — consider re-running the installer\n"
            "with a system-wide Python."
        )
    print()
    print("Restart Claude Code, or type /hooks to reload the config.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
