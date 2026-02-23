---
description: Read Azure DevOps pull requests via smith CLI (read-only)
---

Scope: read-only PR investigation only.

Preflight:
```bash
: "${AZURE_DEVOPS_ORG_URL:?Set AZURE_DEVOPS_ORG_URL first}"
az account show >/dev/null
```

Required arguments:
- For list: `list [filters]`
- For get: `get --project <project> --repo <repo> --id <pr_id>`
- For threads: `threads --project <project> --repo <repo> --id <pr_id>`

First command to run:
- If PR id is known:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" pr get $ARGUMENTS
```
- If PR discussion context is needed:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" pr threads $ARGUMENTS
```
- Otherwise start with listing:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" pr list $ARGUMENTS
```

If no results:
1. Widen date and status filters.
2. Remove restrictive repo or creator filters.
3. Fall back to `code search` to locate related repository paths.

Output contract:
- Return concise PR findings with source paths in `project/repository:path` format when files are referenced.
- Keep scope read-only; do not create comments or approvals.
