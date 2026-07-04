#!/usr/bin/env python3
"""Claude Code status line. Reads JSON on stdin, writes one colored line to stdout.

Displays model + effort, directory + git status, a 16-block partial-fill
context usage bar, the session-limit reset countdown, the weekly-limit
percentage, the session name, and input/output token counts.

Limitations:
- Icons are Nerd Font glyphs — requires a Nerd Font-patched terminal font.
- Git info requires `git` on PATH and a 1.5s budget. Slow repos render the
  branch as 'git…'.
- context_window.used_percentage excludes output tokens (per Claude Code docs).
- Effort is read live from the status line payload (`effort.level`), so
  session-only `/effort max` renders correctly. `/effort ultracode` reports
  as plain `xhigh` in the payload; a best-effort scan of the session
  transcript relabels it `ultracode`, falling back to `xhigh` (which is what
  ultracode officially is). On Claude Code versions that predate the effort
  field, the effort suffix is hidden.
"""

from __future__ import annotations

import json
import os
import re
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
CACHE_MAX_AGE = 3600.0  # prune cache entries older than an hour
TRANSCRIPT_TAIL_BYTES = 256 * 1024


def claude_config_dir() -> Path:
    """Match the install scripts: honor CLAUDE_CONFIG_DIR, else ~/.claude."""
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".claude"


CACHE_FILE = claude_config_dir() / "statusline-cache.json"

# Nerd Font icons (require a Nerd Font-patched terminal font).
ICON_MODEL = "\uf069"  # nf-fa-asterisk (proxy for Anthropic mark)
ICON_FOLDER = "\uf07b"  # nf-fa-folder
ICON_BRANCH = "\ue0a0"  # nf-pl-branch
ICON_CLOCK = "\uf017"  # nf-fa-clock_o
ICON_BOOKMARK = "\uf02e"  # nf-fa-bookmark_o
ICON_ARROW_DOWN = "\uf063"  # nf-fa-arrow_down
ICON_ARROW_UP = "\uf062"  # nf-fa-arrow_up


# --- 256-color ANSI helpers ---
def c256(n: int) -> str:
    return f"\x1b[38;5;{n}m"


RESET = "\x1b[0m"
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
_EFFORT_CMD_RE = re.compile(r"<local-command-stdout>Set effort level to (\w+)")


def _last_effort_command(transcript_path: str | None) -> str | None:
    """Best-effort: the level named by the session's most recent /effort.

    Scans the tail of the transcript for the recorded command output. Real
    /effort records are user-type entries whose content is a plain string
    starting with '<local-command-stdout>Set effort level to …'. Tool
    results and assistant text that merely quote the marker live in nested
    list-shaped content, so string-only matching screens them out.

    The transcript format is undocumented — any surprise returns None and
    the caller falls back to the payload value.
    """
    if not transcript_path:
        return None
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - TRANSCRIPT_TAIL_BYTES))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return None

    last = None
    for line in tail.splitlines():
        if "<local-command-stdout>Set effort level to " not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "user":
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        # .match() anchors at the string start: real command records BEGIN
        # with the marker, while chat text that merely mentions it does not
        # — otherwise a pasted marker could fake (or cancel) the label.
        m = _EFFORT_CMD_RE.match(content)
        if m:
            last = m.group(1)
    return last


def effort_label(data: dict) -> str:
    """Live effort from the payload; empty when the model has no effort knob.

    `/effort ultracode` is not a distinct level — the payload reports it as
    `xhigh` (per the statusline docs). When we see xhigh, check the session
    transcript for the actual command; a failed sniff just shows xhigh.
    """
    effort = data.get("effort")
    if not isinstance(effort, dict):
        return ""
    level = str(effort.get("level") or "")
    if level == "xhigh":
        if _last_effort_command(data.get("transcript_path")) == "ultracode":
            return "ultracode"
    return level


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
        if not isinstance(cache, dict):
            cache = {}
    except Exception:
        cache = {}
    entry = cache.get(cwd)
    if isinstance(entry, dict):
        ts = entry.get("ts")
        if isinstance(ts, (int, float)) and now - ts < CACHE_TTL:
            branch = entry.get("branch")
            if not branch:
                return None
            if branch == "TIMEOUT":
                return ("git…", False)
            return (branch, bool(entry.get("dirty")))

    try:
        # --no-optional-locks: never take the index lock from a background
        # poller — avoids colliding with the user's own git commands.
        proc = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "-C",
                cwd,
                "status",
                "--porcelain=v1",
                "--branch",
                "-z",
            ],
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
    """Atomic, pruned cache write. Concurrent sessions race benignly
    (last writer wins); the pid-suffixed temp file keeps writes from
    interleaving into a corrupt JSON."""
    try:
        now = time.time()
        cache = {
            k: v
            for k, v in cache.items()
            if isinstance(v, dict)
            and isinstance(v.get("ts"), (int, float))
            and now - v["ts"] < CACHE_MAX_AGE
        }
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(cache), encoding="utf-8")
        tmp.replace(CACHE_FILE)
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
def render(data: dict) -> str:
    model = data.get("model") or {}
    model_name = (
        (model.get("display_name") or model.get("id") or "?")
        .removeprefix("Claude ")
        .replace(" context", "")
        .replace(" ", "")
    )

    cwd = data.get("cwd") or (data.get("workspace") or {}).get("current_dir") or ""
    dir_name = Path(cwd).name or cwd or "~"

    ctx = data.get("context_window") or {}
    try:
        pct = float(ctx.get("used_percentage") or 0)
    except (TypeError, ValueError):
        pct = 0.0
    try:
        in_tok = round(float(ctx.get("total_input_tokens") or 0))
        out_tok = round(float(ctx.get("total_output_tokens") or 0))
    except (TypeError, ValueError):
        in_tok = out_tok = 0

    rl = data.get("rate_limits") or {}
    five_hour = rl.get("five_hour") or {}
    seven_day = rl.get("seven_day") or {}
    reset_at = five_hour.get("resets_at")
    weekly_pct = seven_day.get("used_percentage")

    session_label = data.get("session_name") or (data.get("session_id") or "")[:6]

    effort = effort_label(data)
    try:
        cols = int(os.environ.get("COLUMNS") or 200)
    except ValueError:
        cols = 200
    if cols <= 0:  # some environments export COLUMNS=0 when there's no tty
        cols = 200

    # assemble segments
    sep = wrap("▕ ", SEP_COLOR)

    seg_model = (
        wrap(ICON_MODEL, model_color(model_name))
        + " "
        + wrap(model_name, BOLD, model_color(model_name))
        + wrap(effort, CYAN_BRIGHT)
    )

    seg_dir = wrap(ICON_FOLDER, GRAY) + " " + wrap(dir_name, BOLD, WHITE)
    gi = git_info(cwd)
    if gi is not None:
        branch, dirty = gi
        git_str = f"{ICON_BRANCH} {branch}{'*' if dirty else ''}"
        seg_dir += " " + wrap(git_str, GRAY)

    bar, bar_color = render_bar(pct)
    seg_ctx = wrap(bar, bar_color) + " " + wrap(f"{pct:.0f}%", bar_color)

    # A bad field drops its own segment, never the whole line. Broad except
    # is deliberate: e.g. resets_at=1e30 is valid JSON but raises
    # OverflowError in time arithmetic (and OSError on Windows).
    seg_reset = None
    if reset_at is not None:
        try:
            reset_ts = float(reset_at)
            # 5h-window resets are always near; a far-future timestamp is
            # garbage (int() doesn't overflow, it renders a absurd countdown)
            if reset_ts > time.time() + 366 * 86400:
                raise ValueError("implausible resets_at")
            countdown = fmt_countdown(reset_ts)
            seg_reset = wrap(f"{ICON_CLOCK} {countdown}", DIM_GRAY)
        except Exception:
            seg_reset = None

    seg_weekly = None
    if weekly_pct is not None:
        try:
            wp = float(weekly_pct)
            weekly_reset = seven_day.get("resets_at")
            if weekly_reset is not None:
                rst = time.localtime(float(weekly_reset))
                seg_weekly = wrap(f"W{wp:.0f}%", pct_color(wp)) + wrap(
                    f" {ICON_CLOCK} {rst.tm_mon}/{rst.tm_mday}", DIM_GRAY
                )
            else:
                seg_weekly = wrap(f"W{wp:.0f}%", pct_color(wp))
        except Exception:
            seg_weekly = None

    seg_summary = None
    if session_label:
        seg_summary = wrap(f"{ICON_BOOKMARK} {session_label}", BOLD, SUMMARY)

    seg_tok = wrap(
        f"{ICON_ARROW_DOWN} {fmt_tokens(in_tok)} {ICON_ARROW_UP} {fmt_tokens(out_tok)}",
        DIM_GRAY,
    )

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

    return sep.join(parts)


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    try:
        line = render(data)
    except Exception:
        # A status line should degrade, never crash the bar.
        return 0
    try:
        sys.stdout.buffer.write(line.encode("utf-8"))
        sys.stdout.buffer.flush()
    except BrokenPipeError:
        # Consumer went away; point stdout at devnull so the interpreter's
        # shutdown flush doesn't print "Exception ignored" noise to stderr.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
