description: Smith-style targeted grep for Azure DevOps, GitHub, and GitLab repos via smith CLI (read-only)
---

Scope: read-only provider-specific investigation only.

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
- Azure DevOps: `azdo <project> <repo> [pattern] [grep flags]`
- GitHub: `github <repo> [pattern] [grep flags]`
- GitLab: `gitlab <repo> [pattern] [grep flags]`
- Pattern is positional (`"some regex"`).
- Path flags: `--path`, `--glob`, `--from-line`, `--to-line`, `--output-mode`
- GitHub commands take bare `<repo>`, not `org/repo`.
- GitLab commands take `<repo>` relative to configured `GITLAB_GROUP`, not full `group/repo`.

First command to run:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" code grep $ARGUMENTS
```

If no results:
1. If scope is unknown, run discovery first:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" code search "$ARGUMENTS" --take 30
```
2. Narrow or broaden regex, path, or glob as needed.
3. If output is truncated, page using `--from-line` and `--to-line`.
4. If a GitHub command 404s after using `org/repo`, rerun it with bare `<repo>`.
5. If a GitLab command 404s after using full `group/repo`, rerun it with `<repo>` relative to `GITLAB_GROUP`.

Output contract:
- Return concise findings with source paths in `project/repository:path`, `org/repository:path`, or `group/repository:path` format.
- End with a `Sources` section.
- If unresolved, state "not enough evidence" and include one next narrowing grep command.
- Keep scope read-only; do not perform write operations.
