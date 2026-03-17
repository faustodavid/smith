description: Read Azure DevOps and GitHub pull requests via smith CLI (read-only)
---

Scope: read-only provider-specific PR investigation only.

Preflight:
```bash
: "${AZURE_DEVOPS_ORG:?Set AZURE_DEVOPS_ORG first (for azdo)}"
: "${GITHUB_ORG:?Set GITHUB_ORG first (for github)}"
az account show >/dev/null
gh auth status >/dev/null
```

Required arguments:
- List AZDO: `list azdo <project> <repo> [filters]`
- List GitHub: `list github <repo> [filters]`
- Get AZDO: `get azdo <project> <repo> <id>`
- Get GitHub: `get github <repo> <id>`
- Threads AZDO: `threads azdo <project> <repo> <id>`
- Threads GitHub: `threads github <repo> <id>`
- GitHub commands take bare `<repo>`, not `org/repo`.

First command to run:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" prs $ARGUMENTS
```

If no results:
1. Widen date and status filters.
2. Remove restrictive creator filters.
3. Fall back to `smith code search "<broader query>"` to locate related repository paths.
4. If a GitHub command 404s after using `org/repo`, rerun it with bare `<repo>`.

Output contract:
- Return concise PR findings with source paths in `project/repository:path` or `org/repository:path` when files are referenced.
- End with a `Sources` section when file evidence is cited.
- Keep scope read-only; do not create comments or approvals.
