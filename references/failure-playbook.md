# Failure Playbook

Use this playbook when a command path fails.

## 401 or 403 authentication rejected

1. Verify org context:
```bash
echo "$AZURE_DEVOPS_ORG_URL"
```
2. Re-authenticate:
```bash
az login
```
3. Retry the exact same command once.

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
python3 skills/smith/scripts/smith_cli.py code search --query "<broader query>"
```
2. Re-map candidate repo and path.
3. Continue with focused grep.
