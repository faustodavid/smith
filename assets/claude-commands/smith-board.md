description: Read Azure DevOps work items plus GitHub and GitLab issues via smith CLI (read-only)
---

Scope: read-only provider-specific story and issue workflows only. The canonical CLI group is `stories`.

Preflight:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" config list
az account show >/dev/null
gh auth status >/dev/null
glab auth status >/dev/null
```

Required arguments:
- Get AZDO: `<azdo-remote-name> stories get <project> <id>`
- Get GitHub: `<github-remote-name> stories get <repo> <id>`
- Get GitLab: `<gitlab-remote-name> stories get <group/project> <id>`
- Search AZDO: `<azdo-remote-name> stories search <project> --query <text> [filters]`
- Search GitHub: `<github-remote-name> stories search <repo> --query <text> [filters]`
- Search GitLab: `<gitlab-remote-name> stories search <group/project> --query <text> [filters]`
- Mine AZDO: `<azdo-remote-name> stories mine <project> [--include-closed]`
- Mine GitHub: `<github-remote-name> stories mine <repo> [--include-closed]`
- Mine GitLab: `<gitlab-remote-name> stories mine <group/project> [--include-closed]`
- GitHub commands take bare `<repo>`, not `org/repo`.
- GitLab commands take full `group/project` paths.

First command to run:
- If id is known:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" $ARGUMENTS
```
- Otherwise start with search:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" $ARGUMENTS
```

If no results:
1. Broaden search keywords or remove strict filters.
2. Reconfirm the GitHub repo slug is bare `<repo>`.
3. Reconfirm the GitLab repo uses the full `group/project` path.
4. Fall back to `smith code search "<broader query>"` if the wrong repo may have been chosen.
5. If still empty, provide "not enough evidence" and a next command suggestion.

Output contract:
- Return concise findings and include any relevant source paths in `project/repository:path`, `org/repository:path`, or `group/repository:path` format.
- End with a `Sources` section.
- Keep scope read-only; do not create or update work items or issues.
