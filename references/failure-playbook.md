# Failure Playbook

Use this when a read command fails. Use `references/auth-troubleshooting.md` for env or credential setup.

## 401 Or 403

- Confirm `AZURE_DEVOPS_ORG` or `GITHUB_ORG` is set for the target provider.
- Run `az login`.
- For GitHub, use `export GITHUB_TOKEN="<token>"` or `gh auth login`.
- Retry the same command once.

## 429 Or Slow Grep

- Lower `--take`.
- Narrow provider, repo, path, glob, or regex.
- For GitHub grep, reduce `GITHUB_GREP_MAX_WORKERS` or disable parallel fetch.

## Truncated Output

- Narrow `--path` and `--glob`.
- Page with `--from-line` and `--to-line`.
- Reduce `--context-lines`.

## Empty Results

- Broaden the `code search` query.
- Remove strict filters.
- Re-run focused grep on the candidate repo or path.

## Wrong Repo

```bash
smith code search "<broader query>"
```

Then remap the repo and path, and continue with focused grep.

## Unsupported Provider Flow

- Use the closest supported read command for that provider.
- State the fallback explicitly in the answer.
