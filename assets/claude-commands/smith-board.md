---
description: Read Azure DevOps work items via smith CLI (read-only)
---

Scope: read-only board workflows only.

Preflight:
```bash
: "${AZURE_DEVOPS_ORG_URL:?Set AZURE_DEVOPS_ORG_URL first}"
az account show >/dev/null
```

Required arguments:
- Ticket detail: `ticket --project <project> --id <work_item_id>`
- Search/list: `search --query <text> [filters]` or `list --project <project> --wiql "..."`
- Mine: `mine [--project <project>]`

First command to run:
- If id is known:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" board ticket $ARGUMENTS
```
- Otherwise start with search:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" board search $ARGUMENTS
```

If no results:
1. Broaden search keywords or remove strict filters.
2. Use `board list` with WIQL for deterministic querying.
3. If still empty, provide "not enough evidence" and a next command suggestion.

Output contract:
- Return concise findings and include any relevant source paths in `project/repository:path` format when linking to code evidence.
- Keep scope read-only; do not create or update work items.
