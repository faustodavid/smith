# Failure Playbook

Match the failure symptom to a block, apply one step, then retry once. Use `references/auth-troubleshooting.md` for env or credential details.

## 401 or 403

- Confirm the target remote exists in the active config (`smith config show <remote>`).
- Confirm the token env var declared on the remote is set.
- Azure DevOps: `az login`. GitHub: `gh auth login` or `export GITHUB_TOKEN`. GitLab: `glab auth login` or `export GITLAB_TOKEN`.

## 429

- Lower `--take`.
- Narrow with `--path`, `--glob`, or a tighter regex.
- For GitHub grep, reduce `GITHUB_GREP_MAX_WORKERS` or unset `GITHUB_GREP_ENABLE_PARALLEL`.

## Truncation

- Narrow `--path` and `--glob`.
- Page with `--from-line` / `--to-line`.
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
