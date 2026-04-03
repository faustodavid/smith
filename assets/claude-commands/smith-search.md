description: Smith-style Azure DevOps, GitHub, and GitLab discovery search via smith CLI (read-only)
---

Scope: read-only provider-aware investigation only.

Preflight:
```bash
: "${AZURE_DEVOPS_ORG:?Set AZURE_DEVOPS_ORG first (for azdo)}"
: "${GITHUB_ORG:?Set GITHUB_ORG first (for github/all)}"
: "${GITLAB_GROUP:?Set GITLAB_GROUP first (for gitlab/all)}"
az account show >/dev/null
gh auth status >/dev/null
glab auth status >/dev/null
```

Required arguments:
- Query text only, for example `loki retention ttl`.
- Keep initial queries plain and high-signal.
- Do not assume wildcard repo qualifiers such as `org:openai/openai-*`.

First command to run:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" code search "$ARGUMENTS"
```

If no results:
1. Broaden query terms and remove strict operators.
2. Re-run `code search` with a larger take:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" code search "$ARGUMENTS" --take 50
```
3. Optionally scope provider:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" code search "$ARGUMENTS" --provider github
```
4. If still empty, report "not enough evidence" and suggest the next narrower domain keyword.

Output contract:
- Return a concise answer plus source paths in `project/repository:path`, `org/repository:path`, or `group/repository:path`.
- End with a `Sources` section.
- Keep scope read-only; do not suggest write operations.
