#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILL_DIR="$REPO_ROOT/skills/smith"
TARGET_ROOT="${CODEX_HOME:-$HOME/.codex}/skills"
TARGET_LINK="$TARGET_ROOT/smith"

if [[ ! -d "$SKILL_DIR" ]]; then
  echo "Skill directory not found: $SKILL_DIR" >&2
  exit 1
fi

mkdir -p "$TARGET_ROOT"
ln -sfn "$SKILL_DIR" "$TARGET_LINK"

echo "Installed smith skill link: $TARGET_LINK -> $SKILL_DIR"
