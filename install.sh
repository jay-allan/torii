#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> Torii ⛩ setup"

# Install tmux if not present
if ! command -v tmux &>/dev/null; then
    echo "--> Installing tmux..."
    sudo apt install -y tmux
else
    echo "--> tmux already installed: $(tmux -V)"
fi

# Install the torii package (pulls in textual + libtmux as dependencies).
# --user puts the `torii` binary in ~/.local/bin which is on PATH by default.
# -e makes it an editable install so source changes take effect immediately.
echo "--> Installing torii..."
pip install --user --break-system-packages -e .

echo ""
echo "Setup complete!"
echo ""
echo "Make sure ~/.local/bin is on your PATH, then run:  torii"
echo "(If it's missing, add this to ~/.bashrc or ~/.zshrc:)"
echo '  export PATH="$HOME/.local/bin:$PATH"'
