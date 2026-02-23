description: Read Azure DevOps/GitHub board items via smith CLI (read-only)
---

Scope: read-only provider-specific board workflows only (`stories` is an alias).

Preflight:
```bash
: "${AZURE_DEVOPS_ORG_URL:?Set AZURE_DEVOPS_ORG_URL first (for azdo)}"
: "${GITHUB_ORG:?Set GITHUB_ORG first (for github)}"
az account show >/dev/null
```

Required arguments:
- Ticket AZDO: `ticket azdo <project> <id>`
- Ticket GitHub: `ticket github <repo> <id>`
- Search AZDO: `search azdo <project> --query <text> [filters]`
- Search GitHub: `search github <repo> --query <text> [filters]`
- Mine AZDO: `mine azdo <project> [--include-closed]`
- Mine GitHub: `mine github <repo> [--include-closed]`
- List: `list azdo <project> --wiql "..."` (GitHub list unsupported)

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
2. Use `board list azdo <project> --wiql "..."` for deterministic querying.
3. For GitHub, use `board search github <repo> --query "..."` (do not use `board list`).
4. If still empty, provide "not enough evidence" and a next command suggestion.

Output contract:
- Return concise findings and include any relevant source paths in `project/repository:path` format when linking to code evidence.
- Keep scope read-only; do not create or update work items.
