# Failure Playbook

Use this when a read command fails. Use `references/auth-troubleshooting.md` for env or credential setup.

## 401 or 403

- Confirm the target remote exists in the active Smith config.
- Confirm the token env var referenced by that remote is set.
- Run `az login`.
- For GitHub, use `export GITHUB_TOKEN="<token>"` or `gh auth login`.
- For GitLab, use `export GITLAB_TOKEN="<token>"` or `glab auth login`.
- Retry the same command once.

## 429

- Lower `--take`.
- Narrow provider, repo, path, glob, or regex.
- For GitHub grep, reduce `GITHUB_GREP_MAX_WORKERS` or disable parallel fetch.

## Truncation

- Narrow `--path` and `--glob`.
- Page with `--from-line` and `--to-line`.
- Reduce `--context-lines`.

## Empty results

- Broaden the `code search` query.
- Remove strict filters.
- Re-run focused grep on the candidate repo or path.

## Wrong repository

```bash
smith code search "<broader query>"
```

Then remap the repo and path, and continue with focused grep.

For GitHub commands, if you copied `org/repo` from search output and got a 404, rerun the command with the bare `<repo>` slug.

For GitLab commands, if you copied full `group/repo` from search output and got a 404, rerun the command with `<repo>` relative to the configured remote's group.

## Unsupported Provider Flow

- Use the closest supported read command for that provider.
- State the fallback explicitly in the answer.
