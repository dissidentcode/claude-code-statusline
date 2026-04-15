#!/usr/bin/env python3
"""Claude Code status line. Reads JSON on stdin, writes one colored line to stdout.

Displays model + effort, directory + git status, a 16-block partial-fill
context usage bar, the session-limit reset countdown, the weekly-limit
percentage, the session name, and input/output token counts.

Limitations:
- Effort reflects the saved effortLevel in ~/.claude/settings.json. A live
  /effort override is not in the stdin payload and will not be shown until
  the override is persisted.
- Git info requires `git` on PATH and a 1.5s budget. Slow repos render as
  '⏇ git…'.
- context_window.used_percentage excludes output tokens (per Claude Code docs).
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# --- constants ---
BAR_WIDTH = 16
PARTIALS = ["", "▏", "▎", "▍", "▌", "▋", "▊", "▉"]  # 1/8 .. 7/8
FULL = "█"
EMPTY = "░"
GIT_TIMEOUT = 1.5
CACHE_TTL = 2.0
CACHE_FILE = Path.home() / ".claude" / "statusline-cache.json"


# --- 256-color ANSI helpers ---
def c256(n: int) -> str:
    return f"\x1b[38;5;{n}m"


RESET = "\x1b[0m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"

SEP_COLOR = c256(240)
GRAY = c256(244)
DIM_GRAY = c256(245)
WHITE = c256(231)
CYAN_BRIGHT = c256(51)
MAGENTA = c256(165)
SONNET_CYAN = c256(45)
HAIKU_AMBER = c256(214)
GRAY_FALLBACK = c256(250)
SUMMARY = c256(45)

BAR_GREEN = c256(46)
BAR_YELLOW = c256(220)
BAR_RED = c256(196)


def use_color() -> bool:
    return not os.environ.get("NO_COLOR")


def wrap(s: str, *codes: str) -> str:
    if not use_color() or not s:
        return s
    return "".join(codes) + s + RESET


# --- data gathering ---
def load_effort() -> str:
    try:
        data = json.loads(
            (Path.home() / ".claude" / "settings.json").read_text("utf-8")
        )
        return str(data.get("effortLevel", "?"))
    except Exception:
        return "?"


def model_color(display_name: str) -> str:
    low = display_name.lower()
    if "opus" in low:
        return MAGENTA
    if "sonnet" in low:
        return SONNET_CYAN
    if "haiku" in low:
        return HAIKU_AMBER
    return GRAY_FALLBACK


def git_info(cwd: str) -> tuple[str, bool] | None:
    """Return (branch, dirty) or None. 2-second cache keyed by cwd."""
    if not cwd:
        return None
    now = time.time()
    try:
        cache = json.loads(CACHE_FILE.read_text("utf-8"))
    except Exception:
        cache = {}
    entry = cache.get(cwd)
    if entry and now - entry["ts"] < CACHE_TTL:
        if entry["branch"] == "":
            return None
        if entry["branch"] == "TIMEOUT":
            return ("git…", False)
        return (entry["branch"], entry["dirty"])

    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain=v1", "--branch", "-z"],
            capture_output=True,
            timeout=GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        cache[cwd] = {"ts": now, "branch": "TIMEOUT", "dirty": False}
        _write_cache(cache)
        return ("git…", False)
    except FileNotFoundError:
        return None

    if proc.returncode != 0:
        cache[cwd] = {"ts": now, "branch": "", "dirty": False}
        _write_cache(cache)
        return None

    chunks = proc.stdout.split(b"\x00")
    header = chunks[0].decode("utf-8", "replace") if chunks else ""
    branch = "detached"
    if header.startswith("## "):
        rest = header[3:]
        if "(no branch)" in rest:
            branch = "detached"
        else:
            branch = rest.split("...")[0].split(" ")[0]
    dirty = any(c.strip() for c in chunks[1:])
    cache[cwd] = {"ts": now, "branch": branch, "dirty": dirty}
    _write_cache(cache)
    return (branch, dirty)


def _write_cache(cache: dict) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
    except Exception:
        pass


# --- formatting ---
def fmt_tokens(n: int) -> str:
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1_000:
        return f"{n / 1000:.1f}k"
    return str(n)


def pct_color(pct: float) -> str:
    """Green <=50, yellow <=75, red otherwise. Matches the context bar rule."""
    return BAR_GREEN if pct <= 50 else BAR_YELLOW if pct <= 75 else BAR_RED


def render_bar(pct: float) -> tuple[str, str]:
    """16 blocks with partial-block fine fill. Returns (bar, color)."""
    pct = max(0.0, min(100.0, pct))
    total_units = BAR_WIDTH * 8  # 128 fine units
    units = round(pct / 100 * total_units)  # 0..128
    full = units // 8
    partial = units % 8
    bar = FULL * full
    if partial > 0 and full < BAR_WIDTH:
        bar += PARTIALS[partial]
        full += 1
    bar += EMPTY * (BAR_WIDTH - full)
    return bar, pct_color(pct)


def fmt_countdown(resets_at: float) -> str:
    """Format seconds-until-reset as '1h23m' or '45m' or 'now'."""
    delta = int(resets_at - time.time())
    if delta <= 0:
        return "now"
    hours, rem = divmod(delta, 3600)
    minutes = rem // 60
    if hours > 0:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"


# --- main ---
def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    model = data.get("model") or {}
    model_name = (
        (model.get("display_name") or model.get("id") or "?")
        .removeprefix("Claude ")
        .replace(" context", "")
    )

    cwd = data.get("cwd") or (data.get("workspace") or {}).get("current_dir") or ""
    dir_name = Path(cwd).name or cwd or "~"

    ctx = data.get("context_window") or {}
    pct = float(ctx.get("used_percentage") or 0)
    in_tok = int(ctx.get("total_input_tokens") or 0)
    out_tok = int(ctx.get("total_output_tokens") or 0)

    rl = data.get("rate_limits") or {}
    five_hour = rl.get("five_hour") or {}
    seven_day = rl.get("seven_day") or {}
    reset_at = five_hour.get("resets_at")
    weekly_pct = seven_day.get("used_percentage")

    session_name = data.get("session_name") or ""

    effort = load_effort()
    cols = int(os.environ.get("COLUMNS") or 200)

    # assemble segments
    sep = wrap(" ▕ ", SEP_COLOR)

    seg_model = (
        wrap(model_name, BOLD, model_color(model_name))
        + "  "
        + wrap(effort, CYAN_BRIGHT)
    )

    seg_dir = wrap(dir_name, BOLD, WHITE)
    gi = git_info(cwd)
    if gi is not None:
        branch, dirty = gi
        git_str = f"⏇ {branch}{'*' if dirty else ''}"
        seg_dir += "  " + wrap(git_str, GRAY)

    bar, bar_color = render_bar(pct)
    seg_ctx = wrap(bar, bar_color) + " " + wrap(f"{pct:.0f}%", bar_color)

    seg_reset = None
    if reset_at is not None:
        seg_reset = wrap(f"◷ {fmt_countdown(float(reset_at))}", DIM_GRAY)

    seg_weekly = None
    if weekly_pct is not None:
        wp = float(weekly_pct)
        seg_weekly = wrap(f"W{wp:.0f}%", pct_color(wp))

    seg_summary = None
    if session_name:
        seg_summary = wrap(f"❖ {session_name}", BOLD, SUMMARY)

    seg_tok = wrap(f"↓{fmt_tokens(in_tok)} ↑{fmt_tokens(out_tok)}", DIM_GRAY)

    # Order: model, dir, bar, reset countdown, weekly %, session summary, tokens
    parts = [seg_model, seg_dir, seg_ctx]
    if seg_reset is not None:
        parts.append(seg_reset)
    if seg_weekly is not None:
        parts.append(seg_weekly)
    if seg_summary is not None:
        parts.append(seg_summary)
    parts.append(seg_tok)

    # Narrow-terminal degradation — drop from the right in priority order.
    if cols < 130:
        parts = [p for p in parts if p is not seg_tok]
    if cols < 110:
        parts = [p for p in parts if p is not seg_summary]
    if cols < 95:
        parts = [p for p in parts if p is not seg_weekly]
    if cols < 80:
        parts = [p for p in parts if p is not seg_reset]
    if cols < 65:
        parts = [seg_model, seg_dir, wrap(f"{pct:.0f}%", bar_color)]

    sys.stdout.buffer.write(sep.join(parts).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
