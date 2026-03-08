description: Read Azure DevOps/GitHub pull requests via smith CLI (read-only)
---

Scope: read-only provider-specific PR investigation only.

Preflight:
```bash
: "${AZURE_DEVOPS_ORG:?Set AZURE_DEVOPS_ORG first (for azdo)}"
: "${GITHUB_ORG:?Set GITHUB_ORG first (for github)}"
az account show >/dev/null
```

Required arguments:
- List AZDO: `list azdo <project> <repo> [filters]`
- List GitHub: `list github <repo> [filters]`
- Get AZDO: `get azdo <project> <repo> <id>`
- Get GitHub: `get github <repo> <id>`
- Threads AZDO: `threads azdo <project> <repo> <id>`
- Threads GitHub: `threads github <repo> <id>`

First command to run:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" prs $ARGUMENTS
```

If no results:
1. Widen date and status filters.
2. Remove restrictive creator filters.
3. Fall back to `code search` to locate related repository paths.

Output contract:
- Return concise PR findings with source paths in `project/repository:path` format when files are referenced.
- Keep scope read-only; do not create comments or approvals.
