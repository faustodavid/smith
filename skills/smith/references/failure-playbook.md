# Failure Playbook

Match the failure symptom to a block, apply one step, then retry once. Use `references/auth-troubleshooting.md` for env or credential details.

## 401 or 403

- Confirm the target remote exists in the active config (`smith config show <remote>`).
- If the remote declares `token_env`, confirm that env var is set or has a secure-store token.
- If no `token_env` is declared, check the provider runtime fallback: GitHub `GITHUB_TOKEN` or `gh auth login`, GitLab `GITLAB_TOKEN` or `glab auth login`, Azure DevOps `az login`, YouTrack `YOUTRACK_TOKEN`.

## 429

- Lower `--take`.
- Narrow with `--path`, `--glob`, or a tighter regex.
- For GitHub grep, reduce `GITHUB_GREP_MAX_WORKERS` or unset `GITHUB_GREP_ENABLE_PARALLEL`.

## Truncation

- For pipeline logs, try `--reverse` so truncation drops older hits before the latest failures, or page with `--from-line` / `--to-line` if you know the area you need.
- Narrow `--path` and `--glob`.
- Reduce `--context-lines`.

## Empty results

- Broaden the `code search` query (strip rare keywords, drop filters).
- Re-run focused grep on the candidate repo or path.
- For YouTrack, try `stories search --query` with a looser phrase.

## Wrong repository

- Rerun `smith code search "<broader>"` and take the top `repo:path` hit.
- GitHub 404: you likely pasted `org/repo` — retry with the bare `<repo>` slug.
- GitLab 404: you likely used a short name — retry with the full `group/project` path.

## Unsupported Provider Flow

- Use the closest supported read command on that provider.
- State the fallback explicitly in the answer.
