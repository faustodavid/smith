description: Smith-style targeted grep for Azure DevOps/GitHub repos via smith CLI (read-only)
---

Scope: read-only provider-specific investigation only.

Preflight:
```bash
: "${AZURE_DEVOPS_ORG:?Set AZURE_DEVOPS_ORG first (for azdo)}"
: "${GITHUB_ORG:?Set GITHUB_ORG first (for github)}"
az account show >/dev/null
```

Required arguments:
- Azure DevOps: `azdo <project> <repo> [pattern] [grep flags]`
- GitHub: `github <repo> [pattern] [grep flags]`
- Pattern is positional (`"some regex"`).
- Path flags: `--path`, `--glob`, `--from-line`, `--to-line`, `--output-mode`

First command to run:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" code grep $ARGUMENTS
```

If no results:
1. If scope is unknown, run discovery first:
```bash
python3 "$HOME/.codex/skills/smith/scripts/smith_cli.py" code search "$ARGUMENTS" --take 30
```
2. Narrow or broaden regex/path/glob as needed.
3. If output is truncated, page using `--from-line` and `--to-line`.

Output contract:
- Return concise findings with source paths in `project/repository:path` format.
- If unresolved, state "not enough evidence" and include one next narrowing grep command.
- Keep scope read-only; do not perform write operations.
