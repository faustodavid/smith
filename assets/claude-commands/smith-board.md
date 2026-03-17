description: Read Azure DevOps work items and GitHub issues via smith CLI (read-only)
---

Scope: read-only provider-specific story and issue workflows only. The canonical CLI group is `stories`.

Preflight:
```bash
: "${AZURE_DEVOPS_ORG:?Set AZURE_DEVOPS_ORG first (for azdo)}"
: "${GITHUB_ORG:?Set GITHUB_ORG first (for github)}"
az account show >/dev/null
gh auth status >/dev/null
```

Required arguments:
- Get AZDO: `get azdo <project> <id>`
- Get GitHub: `get github <repo> <id>`
- Search AZDO: `search azdo <project> --query <text> [filters]`
- Search GitHub: `search github <repo> --query <text> [filters]`
- Mine AZDO: `mine azdo <project> [--include-closed]`
- Mine GitHub: `mine github <repo> [--include-closed]`
- GitHub commands take bare `<repo>`, not `org/repo`.

First command to run:
- If id is known:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" stories $ARGUMENTS
```
- Otherwise start with search:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" stories $ARGUMENTS
```

If no results:
1. Broaden search keywords or remove strict filters.
2. Reconfirm the GitHub repo slug is bare `<repo>`.
3. Fall back to `smith code search "<broader query>"` if the wrong repo may have been chosen.
4. If still empty, provide "not enough evidence" and a next command suggestion.

Output contract:
- Return concise findings and include any relevant source paths in `project/repository:path` or `org/repository:path` format.
- End with a `Sources` section.
- Keep scope read-only; do not create or update work items or issues.
