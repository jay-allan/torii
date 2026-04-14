#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Prefer venv if it exists (created by setup.sh), otherwise fall back to user Python
if [ -f ".venv/bin/python" ]; then
    exec .venv/bin/python -m torii.main "$@"
else
    exec python3 -m torii.main "$@"
fi
