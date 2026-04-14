"""Entry point for Torii.

If not already inside a tmux session, re-launches itself inside a new
tmux session named 'torii' so that all window management works correctly.
"""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from importlib.metadata import version as _pkg_version
from pathlib import Path

from .sessions import TORII_SESSION, DASHBOARD_NAME, find_claude_sessions, get_torii_session, new_claude_window, switch_to_window
from .monitor import STATUS_FILE

try:
    __version__ = _pkg_version("torii")
except Exception:
    __version__ = "dev"

# Helper scripts written to /tmp at startup; used by tmux keybindings and status bar.
_NEXT_WAITING_SCRIPT = Path("/tmp/torii_next_waiting.sh")
_PREV_WAITING_SCRIPT = Path("/tmp/torii_prev_waiting.sh")
_STATUS_BAR_SCRIPT = Path("/tmp/torii_status.sh")

# Shared Python logic embedded into both jump scripts.
_JUMP_LOGIC = f"""\
import json, subprocess, sys

direction = sys.argv[1]  # "next" or "prev"

try:
    d = json.load(open("{STATUS_FILE}"))
except Exception:
    subprocess.run(["tmux", "display-message", "Torii: status file not found"])
    raise SystemExit

indices = d.get("waiting_indices", [])

if not indices:
    subprocess.run(["tmux", "display-message", "No sessions waiting for input"])
    raise SystemExit

# Find which window is currently active so we can cycle relative to it.
current = subprocess.run(
    ["tmux", "display-message", "-p", "#{{window_index}}"],
    capture_output=True, text=True,
).stdout.strip()

try:
    cur = int(current)
    int_indices = [int(i) for i in indices]
    if direction == "next":
        # First index numerically greater than current; wrap to lowest.
        target = next((i for i in int_indices if i > cur), int_indices[0])
    else:
        # Last index numerically less than current; wrap to highest.
        before = [i for i in int_indices if i < cur]
        target = before[-1] if before else int_indices[-1]
except (ValueError, StopIteration):
    target = int(indices[0])

subprocess.run(["tmux", "select-window", "-t", "{TORII_SESSION}:" + str(target)])
"""


def _write_helper_scripts() -> None:
    """Write small shell scripts that tmux calls for the status bar and jump bindings."""

    for script, direction in [(_NEXT_WAITING_SCRIPT, "next"), (_PREV_WAITING_SCRIPT, "prev")]:
        script.write_text(f"""\
#!/bin/sh
python3 - {direction} <<'EOF'
{_JUMP_LOGIC}
EOF
""")
        script.chmod(0o755)

    _STATUS_BAR_SCRIPT.write_text(f"""\
#!/bin/sh
# Emit a one-line summary for the tmux status bar.
python3 - <<'EOF'
import json
try:
    d = json.load(open("{STATUS_FILE}"))
    t = d.get("total", 0)
    w = d.get("waiting", 0)
    suffix = f"  ⏳ {{w}} waiting" if w else ""
    print(f"⛩  {{t}} sessions{{suffix}}")
except Exception:
    print("⛩  Torii")
EOF
""")
    _STATUS_BAR_SCRIPT.chmod(0o755)


def _register_keybindings() -> None:
    """Register all Torii-specific tmux keybindings and configure the status bar."""

    # Ctrl+T — return to dashboard. Target by index (always 0) not by name,
    # because tmux can rename the window once ToriiApp changes the terminal title.
    subprocess.run(
        ["tmux", "bind-key", "-n", "C-t",
         "select-window", "-t", f"{TORII_SESSION}:0"],
        check=False,
    )

    # Ctrl+C — show a popup instead of sending SIGINT directly.
    # The user can choose to forward the Ctrl+C or switch to the dashboard.
    subprocess.run(
        [
            "tmux", "bind-key", "-n", "C-c",
            "display-menu", "-T", "⛩  Claude Session",
            "Send Ctrl+C to session",  "c", "send-keys C-c",
            "Switch to dashboard",     "t", f"select-window -t {TORII_SESSION}:0",
            "Cancel",                  "Escape", "",
        ],
        check=False,
    )

    # Ctrl+Right / Ctrl+Left — cycle through sessions waiting for input
    subprocess.run(
        ["tmux", "bind-key", "-n", "C-Right",
         "run-shell", str(_NEXT_WAITING_SCRIPT)],
        check=False,
    )
    subprocess.run(
        ["tmux", "bind-key", "-n", "C-Left",
         "run-shell", str(_PREV_WAITING_SCRIPT)],
        check=False,
    )

    # Status bar: show session count + waiting count at the top of every window
    cmds = [
        ["tmux", "set-option", "-g", "status", "on"],
        ["tmux", "set-option", "-g", "status-position", "top"],
        ["tmux", "set-option", "-g", "status-interval", "2"],
        ["tmux", "set-option", "-g", "status-right-length", "60"],
        ["tmux", "set-option", "-g", "status-right", f"#({_STATUS_BAR_SCRIPT})  "],
    ]
    for cmd in cmds:
        subprocess.run(cmd, check=False)


def _resolve_session_intent(
    directory: str | None,
    force_resume: bool,
) -> tuple[str | None, str | None]:
    """Determine target directory and resume ID, prompting when needed.

    Called once, before the tmux bootstrap, in the user's own terminal so the
    prompt is visible.  Returns (target_dir, resume_id):

      - (None, None)          → open the dashboard only, no new Claude window
      - (path, None)          → open a new Claude session in path
      - (path, session_id)    → resume that session in path
    """
    target = Path(directory).resolve() if directory else Path.cwd()
    existing = find_claude_sessions(str(target))

    if not existing:
        # Nothing to resume — honour the original intent.
        return (str(target) if directory else None, None)

    if force_resume:
        # --resume passed explicitly: skip the prompt.
        return (str(target), existing[0]["id"])

    # Found existing sessions — ask the user what to do.
    count = len(existing)
    most_recent = existing[0]
    print(f"\nTorii ⛩  — found {count} existing Claude session(s) in {target}")
    print(f"  Most recent: {most_recent['date']}")
    print()

    while True:
        try:
            raw = input("  [R]esume most recent  [N]ew session  [S]kip: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            return (None, None)

        if raw in ("r", "resume", ""):
            return (str(target), most_recent["id"])
        elif raw in ("n", "new"):
            return (str(target), None)
        elif raw in ("s", "skip"):
            return (None, None)
        else:
            print("  Please enter R, N, or S.")


def _ensure_dashboard() -> None:
    """Restart ToriiApp in window 0 if it is not already running there.

    Uses respawn-pane -k to atomically kill the idle shell and start a fresh
    ToriiApp process — no timing race between send-keys and attach-session.
    """
    result = subprocess.run(
        ["tmux", "display-message", "-p", "#{pane_current_command}",
         "-t", f"{TORII_SESSION}:0"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return  # window 0 doesn't exist; nothing we can do here
    current_cmd = result.stdout.strip()
    if current_cmd in ("python3", "torii", "python"):
        return  # ToriiApp is already running

    torii_cmd = shutil.which("torii")
    if torii_cmd:
        # Single-quote the path for sh; $SHELL is left bare so bash expands it.
        safe = torii_cmd.replace("'", "'\"'\"'")
        respawn_cmd = f"bash -c '{safe}; exec $SHELL'"
    else:
        torii_sh = str(Path(__file__).parent.parent / "torii.sh")
        safe = torii_sh.replace("'", "'\"'\"'")
        respawn_cmd = f"bash '{safe}'; exec $SHELL"

    # respawn-pane -k kills the idle shell and starts ToriiApp atomically.
    # The command is passed as a single string; tmux runs it via /bin/sh -c.
    subprocess.run(
        ["tmux", "respawn-pane", "-k", "-t", f"{TORII_SESSION}:0", respawn_cmd],
        check=False,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="torii",
        description="Torii ⛩ — terminal dashboard for parallel Claude Code sessions",
    )
    parser.add_argument(
        "directory",
        nargs="?",
        help=(
            "Working directory to open a Claude session in immediately. "
            "Relative paths are resolved from the current directory."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume the most recent Claude session in the given directory without prompting.",
    )
    parser.add_argument(
        "--new",
        action="store_true",
        help="Kill any existing Torii session and start a completely fresh one.",
    )
    parser.add_argument(
        "--version", "-V",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    # Internal: carries the resolved session ID through the tmux bootstrap so
    # _resolve_session_intent isn't called a second time inside the session.
    parser.add_argument("--resume-id", dest="resume_id", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # Validate directory early, before any prompting or bootstrapping.
    if args.directory:
        resolved = Path(args.directory).resolve()
        if not resolved.is_dir():
            print(f"torii: '{args.directory}' is not a directory", file=sys.stderr)
            sys.exit(1)

    if not os.environ.get("TMUX"):
        # ── Outer invocation (user's shell) ──────────────────────────────────
        # Prompt about existing sessions now, while we're still in the user's
        # terminal.  --resume-id means we're being called by the bootstrap
        # itself and the decision has already been made — skip the prompt.
        if args.resume_id:
            target_dir = args.directory      # already absolute from bootstrap
            resume_id = args.resume_id
        else:
            target_dir, resume_id = _resolve_session_intent(args.directory, args.resume)

        # --new: kill any existing session before proceeding.
        session_exists = subprocess.run(
            ["tmux", "has-session", "-t", TORII_SESSION],
            capture_output=True,
        ).returncode == 0

        if args.new and session_exists:
            subprocess.run(["tmux", "kill-session", "-t", TORII_SESSION], check=False)
            session_exists = False

        if session_exists:
            # Re-register bindings (picks up any changes from upgrades) then attach.
            _write_helper_scripts()
            _register_keybindings()
            if target_dir:
                session = get_torii_session()
                if session:
                    window = new_claude_window(session, Path(target_dir).name, target_dir, resume_id=resume_id)
                    # Attach directly to the new Claude window, bypassing the dashboard.
                    os.execvp("tmux", ["tmux", "attach-session", "-t", f"{TORII_SESSION}:{window.window_index}"])
            else:
                # No directory: user wants the dashboard. Ensure ToriiApp is
                # running in window 0 — restart it in the shell if it exited.
                _ensure_dashboard()
            os.execvp("tmux", ["tmux", "attach-session", "-t", f"{TORII_SESSION}:0"])
        else:
            # Bootstrap: create the tmux session and re-run Torii inside it,
            # forwarding the resolved intent as explicit arguments.
            extra: list[str] = []
            if target_dir:
                extra.append(target_dir)
            if resume_id:
                extra += ["--resume-id", resume_id]

            torii_cmd = shutil.which("torii")
            if torii_cmd:
                inner = " ".join(shlex.quote(c) for c in [torii_cmd] + extra)
            else:
                torii_sh = str(Path(__file__).parent.parent / "torii.sh")
                inner = " ".join(shlex.quote(c) for c in ["bash", torii_sh] + extra)
            # Wrap with exec $SHELL so window 0 (dashboard) stays alive as a
            # shell after ToriiApp exits — prevents window closure + renumbering
            # that would break the Ctrl+T keybinding.
            cmd = ["tmux", "new-session", "-s", "torii", "-n", "dashboard",
                   "bash", "-c", f"{inner}; exec $SHELL"]
            os.execvp("tmux", cmd)

        sys.exit(1)  # never reached

    # ── Inner invocation (inside tmux) ───────────────────────────────────────
    _write_helper_scripts()
    _register_keybindings()

    if args.directory:
        session = get_torii_session()
        if session:
            window = new_claude_window(
                session,
                Path(args.directory).name,
                args.directory,
                resume_id=args.resume_id,
            )
            switch_to_window(window)

    from .app import ToriiApp
    ToriiApp().run()


if __name__ == "__main__":
    main()
