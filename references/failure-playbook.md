# Failure Playbook

Use this playbook when a command path fails.

## 401 or 403 authentication rejected

1. Verify org context:
```bash
echo "$AZURE_DEVOPS_ORG"
```
2. Re-authenticate:
```bash
az login
```
3. Retry the exact same command once.
4. For GitHub provider, ensure one of:
```bash
export GITHUB_TOKEN="<token>"
# or
gh auth login
```

## 429 rate limited

1. Reduce breadth: lower `--take`.
2. Narrow search path or regex.
3. Retry with scoped command.

## Truncated grep output

1. Narrow with `--path` and `--glob`.
2. Page with `--from-line` and `--to-line`.
3. Retry with reduced context lines if needed.

## Empty results

1. Run broader `code search` terms.
2. Remove strict filters.
3. Re-run focused grep once candidate paths are found.

## Wrong repository selected

1. Return to discovery:
```bash
smith code search "<broader query>"
```
2. Re-map candidate repo and path.
3. Continue with focused grep.

## Unsupported provider flow

If a command is unsupported for a selected provider, use the explicit fallback:

- Prefer the closest supported read command for that provider and restate the fallback explicitly.
