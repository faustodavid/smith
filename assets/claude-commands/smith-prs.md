description: Read Azure DevOps, GitHub, and GitLab pull requests via smith CLI (read-only)
---

Scope: read-only provider-specific PR investigation only.

Preflight:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" config list
az account show >/dev/null
gh auth status >/dev/null
glab auth status >/dev/null
```

Required arguments:
- List AZDO: `<azdo-remote-name> prs list <project> <repo> [filters]`
- List GitHub: `<github-remote-name> prs list <repo> [filters]`
- List GitLab: `<gitlab-remote-name> prs list <group/project> [filters]`
- Get AZDO: `<azdo-remote-name> prs get <project> <repo> <id>`
- Get GitHub: `<github-remote-name> prs get <repo> <id>`
- Get GitLab: `<gitlab-remote-name> prs get <group/project> <id>`
- Threads AZDO: `<azdo-remote-name> prs threads <project> <repo> <id>`
- Threads GitHub: `<github-remote-name> prs threads <repo> <id>`
- Threads GitLab: `<gitlab-remote-name> prs threads <group/project> <id>`
- GitHub commands take bare `<repo>`, not `org/repo`.
- GitLab commands take full `group/project` paths.

First command to run:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" $ARGUMENTS
```

If no results:
1. Widen date and status filters.
2. Remove restrictive creator filters.
3. Fall back to `smith code search "<broader query>"` to locate related repository paths.
4. If a GitHub command 404s after using `org/repo`, rerun it with bare `<repo>`.
5. If a GitLab command 404s after using a short repo name, rerun it with the full `group/project` path.

Output contract:
- Return concise PR findings with source paths in `project/repository:path`, `org/repository:path`, or `group/repository:path` when files are referenced.
- End with a `Sources` section when file evidence is cited.
- Keep scope read-only; do not create comments or approvals.
