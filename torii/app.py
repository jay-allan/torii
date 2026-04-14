"""Textual TUI dashboard for Torii."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, Label, Static

from .monitor import Monitor
from .sessions import (
    delete_window,
    find_claude_sessions,
    get_torii_session,
    list_claude_windows,
    new_claude_window,
    refresh_keybindings,
    switch_to_window,
)

STATUS_ICONS = {
    "waiting": "⏳",
    "working": "⚙ ",
    "idle":    "✓ ",
}


# ---------------------------------------------------------------------------
# New-session modal
# ---------------------------------------------------------------------------

class NewSessionScreen(ModalScreen):
    CSS = """
    NewSessionScreen {
        align: center middle;
    }
    #dialog {
        width: 64;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    #title {
        text-style: bold;
        margin-bottom: 1;
    }
    #session-hint {
        color: $success;
        margin-top: 0;
        margin-bottom: 0;
    }
    #resume {
        margin-top: 0;
        margin-bottom: 1;
    }
    #buttons {
        margin-top: 1;
        height: auto;
        align: right middle;
    }
    #buttons Button {
        margin-left: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("New Claude Session", id="title")
            yield Label("Session name  (optional — defaults to directory name)")
            yield Input(placeholder="e.g. auth-fix", id="name")
            yield Label("Working directory  (leave blank for current directory)")
            yield Input(placeholder=str(Path.cwd()), id="cwd")
            yield Static("", id="session-hint")
            yield Checkbox("Resume most recent session", id="resume", value=False)
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Create", variant="primary", id="create")

    def on_mount(self) -> None:
        # Run an initial check against the current directory
        self._refresh_resume_option("")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "cwd":
            self._refresh_resume_option(event.value)

    def _refresh_resume_option(self, cwd_value: str) -> None:
        cwd = cwd_value.strip() or None
        existing = find_claude_sessions(cwd)
        checkbox = self.query_one("#resume", Checkbox)
        hint = self.query_one("#session-hint", Static)
        if existing:
            hint.update(f"  ↳ {len(existing)} session(s) found — last active {existing[0]['date']}")
            checkbox.disabled = False
            checkbox.value = True
        else:
            hint.update("")
            checkbox.disabled = True
            checkbox.value = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "create":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "name":
            self.query_one("#cwd", Input).focus()
        elif event.input.id == "cwd":
            self._submit()

    def _submit(self) -> None:
        name = self.query_one("#name", Input).value.strip()
        cwd = self.query_one("#cwd", Input).value.strip() or None
        if not name:
            # Auto-generate a name from the directory or a timestamp
            if cwd:
                name = Path(cwd).resolve().name
            else:
                name = Path.cwd().name
            if not name:
                name = f"session-{int(time.time())}"
        resume_id = None
        checkbox = self.query_one("#resume", Checkbox)
        if checkbox.value and not checkbox.disabled:
            existing = find_claude_sessions(cwd)
            if existing:
                resume_id = existing[0]["id"]
        self.dismiss((name, cwd, resume_id))


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

class ToriiApp(App):
    TITLE = "Torii  ⛩"
    CSS = """
    Screen {
        background: $surface;
    }
    DataTable {
        height: 1fr;
    }
    DataTable > .datatable--header {
        text-style: bold;
        background: $primary-darken-2;
    }
    #status-bar {
        height: 1;
        background: $primary-darken-3;
        color: $text-muted;
        padding: 0 1;
    }
    """
    BINDINGS = [
        Binding("n", "new_session", "New"),
        Binding("d", "delete_session", "Delete"),
        Binding("enter", "switch_session", "Switch"),
        Binding("r", "refresh", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.monitor = Monitor()
        self._ordered_keys: list[str] = []
        self._last_summary: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="sessions", zebra_stripes=True)
        yield Static("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        refresh_keybindings()
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns("  #", "Name", "Status", "Last Activity")
        self.set_interval(1.0, self.action_refresh)
        self.action_refresh()

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def action_refresh(self) -> None:
        data = self.monitor.poll()

        # Skip full redraw if nothing changed
        new_summary = {
            k: v["status"] + "|" + v["last_activity"]
            for k, v in data.items()
        }
        if new_summary == self._last_summary:
            return
        self._last_summary = new_summary

        table = self.query_one(DataTable)
        # Remember which key was selected so we can restore the cursor
        prev_key = self._selected_key()

        table.clear()
        self._ordered_keys = sorted(data.keys(), key=lambda k: int(k))

        for key in self._ordered_keys:
            info = data[key]
            status = info["status"]
            icon = STATUS_ICONS.get(status, "? ")
            activity = info["last_activity"] or "—"
            table.add_row(
                f"  {info['index']}",
                info["name"],
                f"{icon}{status.capitalize()}",
                activity,
            )

        # Restore cursor to the same window (or keep at current row)
        if prev_key and prev_key in self._ordered_keys:
            table.move_cursor(row=self._ordered_keys.index(prev_key))

        # Status bar
        total = len(data)
        waiting = sum(1 for v in data.values() if v["status"] == "waiting")
        bar = self.query_one("#status-bar", Static)
        if total == 0:
            bar.update("  No sessions — press [n] to start one")
        elif waiting:
            bar.update(f"  {total} session(s)  ·  ⏳ {waiting} waiting for input")
        else:
            bar.update(f"  {total} session(s) active")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _selected_key(self) -> Optional[str]:
        table = self.query_one(DataTable)
        row = table.cursor_row
        if self._ordered_keys and row is not None and row < len(self._ordered_keys):
            return self._ordered_keys[row]
        return None

    def _selected_window(self):
        key = self._selected_key()
        if key is None:
            return None
        session = get_torii_session()
        if session is None:
            return None
        for w in list_claude_windows(session):
            if w.window_index == key:
                return w
        return None

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_new_session(self) -> None:
        def handle(result):
            if result is None:
                return
            name, cwd, resume_id = result
            session = get_torii_session()
            if session:
                window = new_claude_window(session, name, cwd, resume_id=resume_id)
                switch_to_window(window)

        self.push_screen(NewSessionScreen(), handle)

    def action_quit(self) -> None:
        """Detach from the tmux session, leaving ToriiApp alive in window 0.

        By NOT calling self.exit(), the dashboard process keeps running while
        the user is away. Re-running `torii` re-attaches instantly without
        needing to restart ToriiApp. The session (and all Claude windows)
        continues in the background.
        """
        subprocess.run(["tmux", "detach-client"], check=False)

    def action_delete_session(self) -> None:
        window = self._selected_window()
        if window is None:
            return
        delete_window(window)
        self._last_summary = {}
        self.action_refresh()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_switch_session()

    def action_switch_session(self) -> None:
        window = self._selected_window()
        if window is None:
            return
        switch_to_window(window)
