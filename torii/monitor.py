"""Background status detection and desktop notifications."""
from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .sessions import TORII_SESSION, capture_window_text, get_torii_session, list_claude_windows

# Claude Code renders its input prompt using box-drawing characters.
# These are the tell-tale signs the session is waiting for user input.
_WAITING_INDICATORS = ("╭", "│ >", "│>")

# Characters that indicate a line is purely decorative (box borders, etc.)
_BOX_CHARS = frozenset("╭╰│╮╯─┌└┐┘├┤┬┴┼━═║╔╗╚╝╠╣╦╩╬▶▷◀◁ ")

# Shared status file read by the tmux status bar script and the jump-to-waiting binding.
STATUS_FILE = Path("/tmp/torii-status.json")


@dataclass
class WindowState:
    status: str = "idle"          # "working" | "waiting" | "idle"
    last_text: str = ""
    last_changed: float = field(default_factory=time.monotonic)
    last_activity: str = ""


def _is_waiting(lines: list[str]) -> bool:
    """Return True if the captured output ends with Claude's input prompt."""
    for line in lines[-8:]:
        for indicator in _WAITING_INDICATORS:
            if indicator in line:
                return True
    return False


def _extract_last_activity(text: str) -> str:
    """Return the last meaningful non-decorative line from the pane output."""
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        if all(c in _BOX_CHARS for c in stripped):
            continue
        if stripped in (">", "❯", "$", "%", "#", "▶"):
            continue
        return stripped[:60]
    return ""


def _write_status(result: dict) -> None:
    """Write a compact session summary to the shared status file.

    The tmux status bar script and the jump-to-waiting keybinding both read this.
    """
    waiting_indices = sorted(
        (k for k, v in result.items() if v["status"] == "waiting"),
        key=int,
    )
    try:
        STATUS_FILE.write_text(json.dumps({
            "total": len(result),
            "waiting": len(waiting_indices),
            "waiting_indices": waiting_indices,
        }))
    except OSError:
        pass


def _notify(session_name: str, window_index: str) -> None:
    """Fire a desktop notification in a background thread.

    If the user clicks the 'Switch' action, immediately focus that tmux window.
    Uses notify-send --action which implies --wait; runs in a daemon thread so
    it never blocks the monitor poll loop.
    """
    def _send() -> None:
        result = subprocess.run(
            [
                "notify-send",
                "--action=switch:Switch to session",
                "--icon=utilities-terminal",
                "--urgency=normal",
                "Torii ⛩",
                f"'{session_name}' is waiting for your input",
            ],
            capture_output=True,
            text=True,
        )
        if result.stdout.strip() == "switch":
            subprocess.run(
                ["tmux", "select-window", "-t", f"{TORII_SESSION}:{window_index}"],
                check=False,
            )

    threading.Thread(target=_send, daemon=True).start()


class Monitor:
    """Polls all Claude windows once per call and returns their current state."""

    def __init__(self) -> None:
        self._states: dict[str, WindowState] = {}

    def poll(self) -> dict[str, dict]:
        """
        Returns a dict keyed by window_index containing:
            window, status, last_activity, name, index
        """
        session = get_torii_session()
        if session is None:
            _write_status({})
            return {}

        windows = list_claude_windows(session)
        result: dict[str, dict] = {}

        for window in windows:
            key = window.window_index
            state = self._states.setdefault(key, WindowState())

            text = capture_window_text(window)
            lines = text.splitlines()

            if _is_waiting(lines):
                new_status = "waiting"
            elif text != state.last_text:
                new_status = "working"
            else:
                new_status = "idle"

            # Fire notification on transition → waiting
            if new_status == "waiting" and state.status != "waiting":
                _notify(window.window_name, key)

            if text != state.last_text:
                state.last_text = text
                state.last_changed = time.monotonic()
                state.last_activity = _extract_last_activity(text)

            state.status = new_status

            result[key] = {
                "window": window,
                "status": new_status,
                "last_activity": state.last_activity,
                "name": window.window_name,
                "index": key,
            }

        # Drop state for windows that no longer exist
        live_keys = {w.window_index for w in windows}
        for key in list(self._states):
            if key not in live_keys:
                del self._states[key]

        _write_status(result)
        return result
