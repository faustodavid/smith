#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/faustodavid/smith.git"
INSTALL_DIR="$HOME/.agents/skills/smith"

echo "==> Smith installer"

if ! command -v uv &>/dev/null; then
  echo "Error: uv is not installed."
  echo "Install uv from: https://github.com/astral-sh/uv"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

if ! command -v git &>/dev/null; then
  echo "Error: git is not installed."
  exit 1
fi

mkdir -p "$HOME/.agents/skills"

if [[ -d "$INSTALL_DIR/.git" ]]; then
  echo "==> Updating existing installation at $INSTALL_DIR"
  cd "$INSTALL_DIR"
  git pull --ff-only origin main
else
  echo "==> Cloning smith to $INSTALL_DIR"
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

echo "==> Installing smith CLI globally with uv"
uv tool install -e "$INSTALL_DIR" --force

echo ""
echo "✅ Smith installed successfully!"
echo ""
echo "  Location: $INSTALL_DIR"
echo "  CLI:      smith"
echo "  Update:   cd $INSTALL_DIR && git pull"
echo ""
echo "Try: smith --help"
