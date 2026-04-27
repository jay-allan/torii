# Torii ⛩

> *In Shinto tradition, a torii gate marks the threshold between the everyday world and a sacred,
> focused space. Each time you pass through a torii, you enter a different realm of attention.
> Torii is named for this act of passage — every time you switch into a Claude Code session,
> you step through a gate.*

Torii is a terminal dashboard for Linux that lets you run multiple Claude Code sessions in
parallel and keep track of them all from a single view. Navigate between sessions with arrow
keys, switch to them with Enter, and get desktop notifications the moment one of them is
waiting for your input or finishes a task.

Think of it as the command bridge for your fleet of AI agents.

---

## Features

- **Session overview** — see all your Claude Code sessions at a glance: name, status
  (working / waiting / idle), and time of last activity
- **Keyboard navigation** — arrow keys to select, Enter to jump into that session
- **Notifications** — desktop notification via `notify-send` when Claude asks for your
  input (Waiting) or finishes a task on its own (Idle); clicking the notification switches
  to that session
- **Save & resume dashboard** — press `q` to be prompted to save your current session
  layout to disk; the next time you open Torii in a fresh terminal it will offer to restore
  all sessions automatically
- **New sessions** — press `n` to open a dialog; session name is optional (defaults to
  the directory name), and Torii detects existing Claude sessions in the target directory
  so you can resume them
- **Delete sessions** — press `d` to close a session
- **Mouse scrolling** — scroll up in any Claude window to read back through long responses;
  tmux copy mode activates automatically
- **Terminal tab title** — your terminal emulator's tab shows live status:
  `Torii (3 sessions, 1 working, 2 waiting for input)`
- **tmux status bar** — shows total session count and how many are waiting, visible from
  every window
- **tmux-backed** — each session runs in a real tmux window, so they're robust and
  persist even if Torii's dashboard is not in focus
- **Instant re-attach** — sessions keep running when you detach; running `torii` again
  re-attaches instantly

---

## Requirements

- Linux (tested on Ubuntu 22.04+)
- Python 3.12+
- `notify-send` (`libnotify-bin` — usually pre-installed on GNOME/KDE)
- `tmux` (installed by `install.sh`)
- Claude Code CLI (`claude` command available in `PATH`)

---

## Setup

```bash
git clone <repo> torii
cd torii
bash install.sh
```

This installs `tmux` via `apt` (requires sudo), then installs the `torii` package and its
dependencies into your user environment (`~/.local/bin`). Make sure `~/.local/bin` is on
your `PATH` (it is by default on Ubuntu; if not, add it to `~/.bashrc`):

```bash
export PATH="$HOME/.local/bin:$PATH"
```

---

## Usage

```bash
torii                    # Open (or re-attach to) the dashboard
torii /path/to/project   # Open dashboard and start a Claude session in that directory
torii --resume           # Resume the most recent Claude session in the current directory
torii --new              # Kill any existing Torii session and start fresh
torii --version          # Print version and exit
```

When you run `torii` outside of tmux, it creates a tmux session named `torii` (or
re-attaches to an existing one). The dashboard lives in window 0; every Claude session
gets its own window.

If you run `torii <directory>` and that directory has existing Claude sessions, Torii
will ask whether to resume the most recent one, start a new one, or skip.

If you run `torii` with no arguments, no existing tmux session, and you are not inside a
Claude project directory, Torii will check for a saved dashboard and offer to restore it.

### Keybindings — Dashboard

| Key | Action |
|-----|--------|
| `↑` / `↓` | Navigate sessions |
| `Enter` | Switch to selected session |
| `n` | Create a new Claude session |
| `d` | Delete selected session |
| `r` | Refresh manually |
| `q` | Save dashboard prompt, then detach (sessions keep running in the background) |

### Keybindings — Global (from any window)

| Key | Action |
|-----|--------|
| `Ctrl+T` | Return to the Torii dashboard |
| `Ctrl+C` | Show popup: send Ctrl+C to Claude, switch to dashboard, or cancel |
| `Ctrl+→` | Jump to the next session (wraps around) |
| `Ctrl+←` | Jump to the previous session (wraps around) |

Clicking a desktop notification immediately switches to the session that sent it.

---

## Saving and resuming a dashboard

Pressing `q` on the dashboard opens a prompt with three options:

- **Save & Detach** — writes the current session layout (names and working directories)
  to `~/.config/torii/last_dashboard.json`, then detaches
- **Detach without saving** — detaches without touching the save file
- **Cancel** — returns to the dashboard

The next time you run `torii` with no arguments, no existing tmux session, and from a
directory that is not itself a Claude project, Torii will detect the save file and ask:

```
Torii ⛩  — found a saved dashboard from 2026-04-17T14:30:00Z
  2 session(s):
    · auth-fix  (/home/jay/Dev/myproject)
    · api-refactor  (/home/jay/Dev/api)

  [R]esume saved dashboard  [N]ew session:
```

Choosing `R` recreates all sessions (resuming each directory's most recent Claude
conversation). Sessions whose directories no longer exist are silently skipped.

---

## How status detection works

Torii polls each Claude session once per second using `tmux capture-pane`, reads the
last 20 lines of terminal output, strips ANSI escape codes, and classifies the state:

| State | Indicator |
|-------|-----------|
| **Waiting** | Claude's input prompt box (`╭...╮`) is visible in the last few lines |
| **Working** | Terminal output has changed since the last poll |
| **Idle** | Output is unchanged and no prompt is visible |

A desktop notification fires on two transitions:
- Any state → **Waiting** (Claude needs your input)
- **Working** → **Idle** (Claude finished a task autonomously)

---

## Project layout

```
torii/
├── README.md
├── DEVELOPMENT.md       # Architecture and developer notes
├── pyproject.toml       # Package metadata + entry point (installs `torii` command)
├── install.sh           # Install tmux + pip install -e .
├── torii.sh             # Direct launcher (no install required)
└── torii/
    ├── main.py          # Entry point; bootstraps tmux session
    ├── app.py           # Textual TUI dashboard
    ├── sessions.py      # libtmux helpers (including dashboard save/load)
    └── monitor.py       # Status polling + notifications
```
