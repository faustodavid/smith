description: Read and grep Azure DevOps/GitHub build logs via smith CLI (read-only)
---

Scope: read-only provider-specific build-log investigation only.

Preflight:
```bash
: "${AZURE_DEVOPS_ORG:?Set AZURE_DEVOPS_ORG first (for azdo)}"
: "${GITHUB_ORG:?Set GITHUB_ORG first (for github)}"
az account show >/dev/null
```

Required arguments:
- Metadata AZDO: `logs azdo <project> <id>`
- Metadata GitHub: `logs github <repo> <id>`
- Grep AZDO: `grep azdo <project> <id> [--log-id <n>] --pattern <regex>`
- Grep GitHub: `grep github <repo> <id> [--log-id <n>] --pattern <regex>`

First command to run:
- Start with metadata:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" build logs $ARGUMENTS
```
- Then narrow with grep:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" build grep $ARGUMENTS
```

If no results:
1. Search all logs with broader pattern.
2. Switch to `--output-mode logs_with_matches`.
3. Page with `--from-line` and `--to-line` for large logs.

Output contract:
- Return concise findings and, when relevant, include repository source paths in `project/repository:path` format discovered during corroboration.
- Keep scope read-only; do not trigger write operations.
