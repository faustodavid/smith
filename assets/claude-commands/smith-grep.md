---
description: Thanos-style targeted grep for Azure DevOps repos via smith CLI (read-only)
---

Scope: read-only Azure DevOps investigation only.

Preflight:
```bash
: "${AZURE_DEVOPS_ORG_URL:?Set AZURE_DEVOPS_ORG_URL first}"
az account show >/dev/null
```

Required arguments:
- Preferred shape:
`--project <project> --repo <repo> --pattern <regex> [--path <path>] [--glob <glob>] [extra flags]`

First command to run:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" code grep $ARGUMENTS
```

If no results:
1. If project or repo is unknown, run discovery first:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" code search --query "$ARGUMENTS" --take 30
```
2. Narrow or broaden regex/path/glob as needed.
3. If output is truncated, page using `--from-line` and `--to-line`.

Output contract:
- Return concise findings with source paths in `project/repository:path` format.
- If unresolved, state "not enough evidence" and include one next narrowing grep command.
- Keep scope read-only; do not perform write operations.
