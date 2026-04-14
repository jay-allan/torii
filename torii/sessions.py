"""tmux session and window management via libtmux."""
from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

import libtmux

TORII_SESSION = "torii"
DASHBOARD_NAME = "dashboard"

_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def get_server() -> libtmux.Server:
    return libtmux.Server()


def get_torii_session(server: Optional[libtmux.Server] = None) -> Optional[libtmux.Session]:
    if server is None:
        server = get_server()
    for session in server.sessions:
        if session.session_name == TORII_SESSION:
            return session
    return None


def list_claude_windows(session: libtmux.Session) -> list[libtmux.Window]:
    """All windows except the dashboard (window 0 / named 'dashboard')."""
    return [
        w for w in session.windows
        if w.window_name != DASHBOARD_NAME and w.window_index != "0"
    ]


def find_claude_sessions(cwd: Optional[str] = None) -> list[dict]:
    """Return existing Claude Code sessions for a working directory, newest first.

    Each entry has: id, date (human-readable), mtime (float).
    """
    resolved = Path(cwd).resolve() if cwd else Path.cwd()
    # Claude encodes paths by replacing every '/' with '-' (leading slash → leading dash)
    encoded = str(resolved).replace("/", "-")
    projects_dir = Path.home() / ".claude" / "projects" / encoded

    if not projects_dir.exists():
        return []

    entries = []
    for jsonl in projects_dir.glob("*.jsonl"):
        mtime = jsonl.stat().st_mtime
        entries.append({
            "id": jsonl.stem,
            "date": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
            "mtime": mtime,
        })

    return sorted(entries, key=lambda e: e["mtime"], reverse=True)


def new_claude_window(
    session: libtmux.Session,
    name: str,
    cwd: Optional[str] = None,
    resume_id: Optional[str] = None,
) -> libtmux.Window:
    """Open a new tmux window and start Claude in it.

    If resume_id is given, resumes that session; otherwise starts fresh.
    """
    kwargs: dict = {"window_name": name, "attach": False}
    if cwd:
        kwargs["start_directory"] = cwd
    window = session.new_window(**kwargs)
    pane = window.active_pane
    if resume_id:
        pane.send_keys(f"claude --resume '{resume_id}'")
    else:
        pane.send_keys("claude")
    return window


def refresh_keybindings() -> None:
    """Re-register the Ctrl+T dashboard keybinding (idempotent, safe to call anytime)."""
    subprocess.run(
        ["tmux", "bind-key", "-n", "C-t", "select-window", "-t", f"{TORII_SESSION}:0"],
        check=False,
    )


def delete_window(window: libtmux.Window) -> None:
    try:
        window.kill()
    except AttributeError:
        window.kill_window()  # older libtmux


def switch_to_window(window: libtmux.Window) -> None:
    """Switch the tmux client to the given window."""
    target = f"{window.session_name}:{window.window_index}"
    subprocess.run(["tmux", "select-window", "-t", target], check=False)


def strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


def capture_window_text(window: libtmux.Window, lines: int = 20) -> str:
    """Capture the last N lines of a tmux window, ANSI codes stripped."""
    target = f"{window.session_name}:{window.window_index}"
    result = subprocess.run(
        ["tmux", "capture-pane", "-pt", target, "-S", f"-{lines}"],
        capture_output=True,
        text=True,
    )
    return strip_ansi(result.stdout)
