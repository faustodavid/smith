description: Thanos-style Azure DevOps + GitHub discovery search via smith CLI (read-only)
---

Scope: read-only provider-aware investigation only.

Preflight:
```bash
: "${AZURE_DEVOPS_ORG_URL:?Set AZURE_DEVOPS_ORG_URL first (for azdo)}"
: "${GITHUB_ORG:?Set GITHUB_ORG first (for github/all)}"
az account show >/dev/null
```

Required arguments:
- Query text only, for example `loki retention ttl`.

First command to run:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" code search --query "$ARGUMENTS"
```

If no results:
1. Broaden query terms and remove strict operators.
2. Re-run `code search` with a larger take:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" code search --query "$ARGUMENTS" --take 50
```
3. Optionally scope provider:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" code search --query "$ARGUMENTS" --provider github
```
4. If still empty, report "not enough evidence" and suggest the next narrower domain keyword.

Output contract:
- Return a concise answer plus source paths in `project/repository:path` format.
- Keep scope read-only; do not suggest write operations.
