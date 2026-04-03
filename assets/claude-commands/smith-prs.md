description: Read Azure DevOps, GitHub, and GitLab pull requests via smith CLI (read-only)
---

Scope: read-only provider-specific PR investigation only.

Preflight:
```bash
: "${AZURE_DEVOPS_ORG:?Set AZURE_DEVOPS_ORG first (for azdo)}"
: "${GITHUB_ORG:?Set GITHUB_ORG first (for github)}"
: "${GITLAB_GROUP:?Set GITLAB_GROUP first (for gitlab)}"
az account show >/dev/null
gh auth status >/dev/null
glab auth status >/dev/null
```

Required arguments:
- List AZDO: `list azdo <project> <repo> [filters]`
- List GitHub: `list github <repo> [filters]`
- List GitLab: `list gitlab <repo> [filters]`
- Get AZDO: `get azdo <project> <repo> <id>`
- Get GitHub: `get github <repo> <id>`
- Get GitLab: `get gitlab <repo> <id>`
- Threads AZDO: `threads azdo <project> <repo> <id>`
- Threads GitHub: `threads github <repo> <id>`
- Threads GitLab: `threads gitlab <repo> <id>`
- GitHub commands take bare `<repo>`, not `org/repo`.
- GitLab commands take `<repo>` relative to configured `GITLAB_GROUP`, not full `group/repo`.

First command to run:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" prs $ARGUMENTS
```

If no results:
1. Widen date and status filters.
2. Remove restrictive creator filters.
3. Fall back to `smith code search "<broader query>"` to locate related repository paths.
4. If a GitHub command 404s after using `org/repo`, rerun it with bare `<repo>`.
5. If a GitLab command 404s after using full `group/repo`, rerun it with `<repo>` relative to `GITLAB_GROUP`.

Output contract:
- Return concise PR findings with source paths in `project/repository:path`, `org/repository:path`, or `group/repository:path` when files are referenced.
- End with a `Sources` section when file evidence is cited.
- Keep scope read-only; do not create comments or approvals.
