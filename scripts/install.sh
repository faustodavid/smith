#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/faustodavid/smith.git"
REPO_DIR="$HOME/.local/share/smith"
SKILL_DIR="$REPO_DIR/skills/smith"
TARGET_SKILL_DIR="$HOME/.agents/skills/smith"

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

mkdir -p "$HOME/.local/share"
mkdir -p "$HOME/.agents/skills"

if [[ -d "$REPO_DIR/.git" ]]; then
  echo "==> Updating repo checkout at $REPO_DIR"
  git -C "$REPO_DIR" pull --ff-only origin main
else
  echo "==> Cloning smith to $REPO_DIR"
  rm -rf "$REPO_DIR"
  git clone "$REPO_URL" "$REPO_DIR"
fi

if [[ ! -d "$SKILL_DIR" ]]; then
  echo "Error: skill directory not found after install: $SKILL_DIR" >&2
  exit 1
fi

echo "==> Syncing skill to $TARGET_SKILL_DIR"
rm -rf "$TARGET_SKILL_DIR"
cp -R "$SKILL_DIR" "$TARGET_SKILL_DIR"

echo "==> Installing smith CLI globally with uv"
uv tool install -e "$REPO_DIR" --force

echo ""
echo "✅ Smith installed successfully!"
echo ""
echo "  Repo:     $REPO_DIR"
echo "  Skill:    $TARGET_SKILL_DIR"
echo "  CLI:      smith"
echo "  Update:   bash $REPO_DIR/scripts/install.sh"
echo ""
echo "Try: smith --help"

