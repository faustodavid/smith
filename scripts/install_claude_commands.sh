#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_DIR="$SKILL_DIR/assets/claude-commands"
TARGET_REPO="${1:-$(pwd)}"
MODE="${2:-copy}"
TARGET_DIR="$TARGET_REPO/.claude/commands"

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "Source command directory not found: $SOURCE_DIR" >&2
  exit 1
fi

mkdir -p "$TARGET_DIR"

for src in "$SOURCE_DIR"/*.md; do
  base="$(basename "$src")"
  dst="$TARGET_DIR/$base"

  if [[ "$MODE" == "symlink" ]]; then
    ln -sfn "$src" "$dst"
  else
    install -m 0644 "$src" "$dst"
  fi

done

echo "Installed Claude commands into: $TARGET_DIR (mode=$MODE)"
