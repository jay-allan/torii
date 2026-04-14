# Torii — Developer Notes

---

## Architecture

### Two-path invocation model

`main.py` has two distinct execution paths depending on whether the process is already
inside a tmux session (`$TMUX` environment variable):

**Outer path** (user's shell, no tmux):
1. Optionally prompt about existing Claude sessions (resume / new / skip)
2. If a `torii` tmux session already exists: re-register keybindings, optionally create
   a new Claude window, then `os.execvp` into `tmux attach-session -t torii:0`
3. If no session exists: `os.execvp` into `tmux new-session` which re-runs `torii` inside

**Inner path** (inside tmux, after bootstrap):
1. Write helper scripts to `/tmp/`
2. Register all keybindings and configure the tmux status bar
3. Optionally open a Claude window if a directory argument was given
4. Launch `ToriiApp` (Textual)

The outer path always calls `os.execvp` — it replaces itself with the tmux process so
there is no lingering wrapper.

---

### Window layout

```
tmux session: torii
├── window 0 "dashboard"   ← ToriiApp (Textual TUI) — lives here permanently
├── window 1 "project-a"   ← claude
├── window 2 "project-b"   ← claude
└── ...
```

Window 0 is special: it must always contain a running ToriiApp process. This invariant
is enforced by two mechanisms:

1. **Bootstrap wrapper**: the `new-session` command is `bash -c "torii; exec $SHELL"`.
   If ToriiApp ever crashes or exits, the `exec $SHELL` fallback keeps window 0 alive as
   an interactive shell rather than closing it (which would cause tmux to renumber windows
   and break the `Ctrl+T` binding that hard-codes `:0`).

2. **`_ensure_dashboard()`**: called on every outer-path attach. Checks
   `#{pane_current_command}` of window 0. If it is not `python3`/`torii`/`python`, it
   uses `tmux respawn-pane -k` to atomically restart ToriiApp. This handles the crash
   recovery scenario; under normal operation it is a no-op.

---

### Why ToriiApp never calls `self.exit()`

`q` in the dashboard calls `action_quit`, which only runs `tmux detach-client`. It does
**not** call `self.exit()`. This is a deliberate design decision:

- ToriiApp stays alive in window 0 at all times
- Re-attaching (`torii` with no args) is instant — the dashboard is already running
- `_ensure_dashboard()` always sees `pane_current_command == "python3"` and skips the
  restart logic, eliminating a whole class of timing and quoting bugs

If `self.exit()` were called, the `exec $SHELL` fallback would run, window 0 would show
a shell prompt, and `_ensure_dashboard()` would need to restart ToriiApp via
`respawn-pane` — which has fragile quoting requirements (see Lessons Learned).

---

### Keybindings and the status bar

All tmux keybindings are registered via `_register_keybindings()` (called on the inner
path) and `refresh_keybindings()` (called from `ToriiApp.on_mount` every time the
dashboard starts, as a safety net).

The `Ctrl+→` / `Ctrl+←` jump bindings call small Python scripts written to `/tmp/`:

- `/tmp/torii_next_waiting.sh`
- `/tmp/torii_prev_waiting.sh`
- `/tmp/torii_status.sh` (status bar)

These scripts read `/tmp/torii_status.json` (written by `monitor.py`) to find which
windows are waiting. They are regenerated on every `torii` invocation.

The tmux status bar is set to a 2-second refresh interval, showing a one-line summary
from `torii_status.sh`.

---

### Status detection (monitor.py)

`Monitor.poll()` is called by `ToriiApp` via `set_interval(1.0, ...)`. For each Claude
window it:

1. Captures the last 20 lines via `tmux capture-pane`
2. Strips ANSI escape codes
3. Classifies status:
   - **Waiting**: Claude's input prompt box (`╭`) visible in the last few lines
   - **Working**: captured text differs from the previous poll
   - **Idle**: text unchanged, no prompt visible
4. Fires `notify-send` on any `→ waiting` transition

The `Monitor` class also writes a JSON file (`/tmp/torii_status.json`) containing
`total`, `waiting`, and `waiting_indices` for the helper scripts to consume.

---

### Session resume detection

`find_claude_sessions(cwd)` in `sessions.py` looks in
`~/.claude/projects/<encoded-path>/` for `.jsonl` files. Claude encodes project paths
by replacing every `/` with `-`. Sessions are sorted by `mtime`, newest first.

This is used both in the CLI (`_resolve_session_intent`) and in the new-session modal
(`NewSessionScreen._refresh_resume_option`) to offer resume options to the user.

---

## Known issues / fragilities

### `pane_current_command` detection is heuristic

`_ensure_dashboard()` checks whether the command in window 0 is `python3`, `torii`, or
`python`. This covers the common cases but could miss edge cases (e.g., a wrapper script
that shows a different name, or a future venv layout change). If detection fails, the
function silently returns without restarting, leaving the user at a shell prompt.

### Window 0 is hard-coded everywhere

`Ctrl+T`, `_ensure_dashboard()`, and the outer-path `attach-session` all reference
`torii:0` by index. If window 0 is somehow closed (e.g., manual `tmux kill-window`),
these will fail silently. `renumber-windows` is off by default in tmux, so window
indices are stable during normal operation.

### `list_claude_windows` dual-exclusion logic

```python
if w.window_name != DASHBOARD_NAME and w.window_index != "0"
```

Both conditions are checked because window names can be changed by tmux (e.g., when the
terminal title changes). If the dashboard window is ever renamed, only the index check
saves us. If `renumber-windows` were on, the index would change and only the name check
would save us. Keep both.

### Helper scripts use hard-coded `STATUS_FILE` path

The jump scripts and status bar script embed the path to the status JSON file at
write-time. If `STATUS_FILE` (in `monitor.py`) is ever changed, `_write_helper_scripts`
in `main.py` must be updated accordingly (it imports the constant, so it will stay in
sync automatically — but the on-disk scripts from a previous run will be stale until the
next `torii` invocation regenerates them).

### `notify-send --action` click-to-focus

Desktop notification click-through (switching to the waiting window on click) uses
`notify-send --action`. This requires `notify-send` v0.8+ and a notification daemon that
supports actions (e.g., dunst, GNOME Shell). On systems without action support the
notification still fires but clicking it does nothing.

---

## What to watch out for when developing further

### Shell quoting in `respawn-pane`

`tmux respawn-pane -k -t TARGET COMMAND` passes the command through `/bin/sh -c`. This
is one shell layer more than `tmux new-session ... bash -c CMD` (which is two shell
layers total: tmux → bash → program). When constructing the `respawn-pane` command:

- Use manual single-quote escaping (`path.replace("'", "'\"'\"'")`) to embed a path
  safely inside a `bash -c '...'` string — **not** `shlex.quote()`, which would add its
  own quotes and produce unbalanced quoting.
- Leave `$SHELL` bare (not quoted) so it is expanded by the shell, not passed literally.

`shlex.quote()` is for building shell command strings that will be passed as a single
argument to another Python `subprocess` call. It is NOT for embedding a value inside a
shell script string that will itself be parsed by `/bin/sh`.

### DataTable and the Enter key in Textual

Textual's `DataTable` widget intercepts the Enter key internally and fires
`DataTable.RowSelected` before the `App`-level keybinding system sees it. Defining an
`action_switch_session` binding for `"enter"` in `BINDINGS` will **not** fire when a
DataTable row is highlighted. Always handle `on_data_table_row_selected` instead.

### `self.exit()` and the persistent-dashboard contract

Any code path that calls `self.exit()` breaks the "ToriiApp lives forever in window 0"
invariant and will eventually produce a shell prompt instead of a dashboard on re-attach.
If you add any shutdown logic, ensure it only calls `tmux detach-client` and leaves the
Textual app running.

### Textual version compatibility

The project targets `textual>=0.80.0`. The `DataTable` API (column/row management,
cursor type, `RowSelected` event) changed significantly between 0.x versions. If
upgrading Textual, verify that `table.cursor_type = "row"`, `table.add_columns()`,
`table.add_row()`, `table.clear()`, and `table.move_cursor()` still behave as expected.

---

## Lessons learned

### The "ToriiApp must never exit" insight

The root cause of most "empty terminal on re-attach" bugs was that `action_quit` called
`self.exit()`, causing ToriiApp to exit, `exec $SHELL` to run, and window 0 to become a
shell. Every subsequent fix attempt (send-keys timing, `respawn-pane` quoting) was
treating a symptom. The real fix was to never call `self.exit()` at all.

Lesson: if a piece of state needs to be kept alive, make that the invariant and design
around it — don't try to detect-and-restart.

### `tmux new-session` multi-arg vs single-arg

```bash
# Multi-arg: tmux execs the program directly (no extra shell layer)
tmux new-session -s torii -n dashboard bash -c "torii; exec $SHELL"

# Single-arg string: also fine, tmux passes it to /bin/sh -c
tmux new-session -s torii -n dashboard "bash -c 'torii; exec $SHELL'"
```

But `respawn-pane` always passes the command as a single string through `/bin/sh -c`.
Keep these separate mental models to avoid quoting confusion.

### Detecting dashboard liveness

`#{pane_current_command}` gives the *leaf* process name, not the full command line. A
Python process running `torii` will show `python3` (or `python`), not `torii`, because
`torii` is a setuptools-generated wrapper script that execs Python. This is fine for
detection purposes but means the check must include all three names.

### Incremental debugging strategy

When a bug involves tmux + process management + Python all interacting, the fastest path
is to inspect tmux state directly:

```bash
tmux list-windows -t torii          # Are all expected windows present?
tmux display-message -p "#{pane_current_command}" -t torii:0  # What's in window 0?
tmux show-options -g status-right   # Is the status bar configured?
```

These commands can be run from outside the tmux session and provide ground truth faster
than log files or print statements.
