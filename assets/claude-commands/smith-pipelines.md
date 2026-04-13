description: Read and grep Azure DevOps, GitHub, and GitLab pipeline logs via smith CLI (read-only)
---

Scope: read-only provider-specific pipeline-log investigation only.

Preflight:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" config list
az account show >/dev/null
gh auth status >/dev/null
glab auth status >/dev/null
```

Required arguments:
- List AZDO: `<azdo-remote-name> pipelines logs list <project> <id>`
- List GitHub: `<github-remote-name> pipelines logs list <repo> <id>`
- List GitLab: `<gitlab-remote-name> pipelines logs list <group/project> <id>`
- Grep AZDO: `<azdo-remote-name> pipelines logs grep <project> <id> <regex> [--log-id <n>]`
- Grep GitHub: `<github-remote-name> pipelines logs grep <repo> <id> <regex> [--log-id <n>]`
- Grep GitLab: `<gitlab-remote-name> pipelines logs grep <group/project> <id> <regex> [--log-id <n>]`
- GitHub commands take bare `<repo>`, not `org/repo`.
- GitLab commands take full `group/project` paths.

First command to run:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" $ARGUMENTS
```

If no results:
1. Search all logs with a broader pattern.
2. Switch to `--output-mode logs_with_matches`.
3. Page with `--from-line` and `--to-line` for large logs.
4. If a GitHub command 404s after using `org/repo`, rerun it with bare `<repo>`.
5. If a GitLab command 404s after using a short repo name, rerun it with the full `group/project` path.

Output contract:
- Return concise findings and, when relevant, include repository source paths in `project/repository:path`, `org/repository:path`, or `group/repository:path`.
- End with a `Sources` section when file or log evidence is cited.
- Keep scope read-only; do not trigger write operations.
