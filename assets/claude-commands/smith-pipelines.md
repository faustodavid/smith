description: Read and grep Azure DevOps, GitHub, and GitLab pipeline logs via smith CLI (read-only)
---

Scope: read-only provider-specific pipeline-log investigation only.

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
- List AZDO: `list azdo <project> <id>`
- List GitHub: `list github <repo> <id>`
- List GitLab: `list gitlab <repo> <id>`
- Grep AZDO: `grep azdo <project> <id> <regex> [--log-id <n>]`
- Grep GitHub: `grep github <repo> <id> <regex> [--log-id <n>]`
- Grep GitLab: `grep gitlab <repo> <id> <regex> [--log-id <n>]`
- GitHub commands take bare `<repo>`, not `org/repo`.
- GitLab commands take `<repo>` relative to configured `GITLAB_GROUP`, not full `group/repo`.

First command to run:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" pipelines logs $ARGUMENTS
```

If no results:
1. Search all logs with a broader pattern.
2. Switch to `--output-mode logs_with_matches`.
3. Page with `--from-line` and `--to-line` for large logs.
4. If a GitHub command 404s after using `org/repo`, rerun it with bare `<repo>`.
5. If a GitLab command 404s after using full `group/repo`, rerun it with `<repo>` relative to `GITLAB_GROUP`.

Output contract:
- Return concise findings and, when relevant, include repository source paths in `project/repository:path`, `org/repository:path`, or `group/repository:path`.
- End with a `Sources` section when file or log evidence is cited.
- Keep scope read-only; do not trigger write operations.
