# Usage Recipes

Use these patterns after the trigger is confirmed. If a command fails, open `references/failure-playbook.md`.

## Preflight

```bash
export AZURE_DEVOPS_ORG="<org>"
export GITHUB_ORG="<org>"
export GITLAB_GROUP="<group>"
az login
gh auth status
glab auth status
```

GitHub repo-shape rule:

- Search results look like `org/repo:path`.
- GitHub command arguments use bare `<repo>`, not `org/repo`.
- Example: `smith code grep <github-remote> openai-python "OPENAI_WEBHOOK_SECRET"`

GitLab repo-shape rule:

- Search results look like `group/repo:path`.
- GitLab command arguments use `<repo>` relative to configured `GITLAB_GROUP`, not full `group/repo`.
- Example: `smith code grep <gitlab-remote> api "CI_JOB_TOKEN"`

## Default Loop

1. Discover candidates:
```bash
smith code search "<keywords>" --take 30
```
2. Map structure only when repo is known but file scope is not:
```bash
smith code grep <azdo-remote> <project> <repo> ".*" --output-mode files_with_matches
smith code grep <github-remote> <repo> ".*" --output-mode files_with_matches
smith code grep <gitlab-remote> <repo> ".*" --output-mode files_with_matches
```
3. Extract proof from the smallest useful scope:
```bash
smith code grep <azdo-remote> <project> <repo> "<regex>" --path <path> --glob "<glob>" --context-lines 2
smith code grep <github-remote> <repo> "<regex>" --path <path> --glob "<glob>" --context-lines 2
smith code grep <gitlab-remote> <repo> "<regex>" --path <path> --glob "<glob>" --context-lines 2
```
4. Corroborate with `prs`, `pipelines logs`, or `stories` only when code evidence is not enough on its own.
5. Report exact `project/repository:path` or `org/repository:path` evidence. If unresolved, say `not enough evidence` and give one next command.

## Query Discipline

- Start with plain, high-signal search terms.
- Prefer another simple search over one overloaded query string.
- Do not assume wildcard repo qualifiers such as `org:openai/openai-*`.
- If search already returned the target file, skip repo-wide `".*"` dumps and go straight to focused grep.

## Discovery Helpers

```bash
smith orgs <remote>
smith repos <azdo-remote>
smith repos <azdo-remote> <project>
smith repos <github-remote>
smith repos <gitlab-remote>
```

## Pull Requests

```bash
smith prs list <azdo-remote> <project> <repo> --status active,completed --take 25
smith prs get <azdo-remote> <project> <repo> <id>
smith prs threads <azdo-remote> <project> <repo> <id>
smith prs list <github-remote> <repo> --status active,completed
smith prs get <github-remote> <repo> <id>
smith prs threads <github-remote> <repo> <id>
smith prs list <gitlab-remote> <repo> --status active,completed
smith prs get <gitlab-remote> <repo> <id>
smith prs threads <gitlab-remote> <repo> <id>
```

## Pipeline Logs

```bash
smith pipelines logs list <azdo-remote> <project> <id>
smith pipelines logs grep <azdo-remote> <project> <id> "ERROR|Exception" --output-mode logs_with_matches
smith pipelines logs grep <azdo-remote> <project> <id> ".*" --log-id <log_id> --from-line <n>
smith pipelines logs list <github-remote> <repo> <id>
smith pipelines logs grep <github-remote> <repo> <id> "ERROR|Exception"
smith pipelines logs list <gitlab-remote> <repo> <id>
smith pipelines logs grep <gitlab-remote> <repo> <id> "ERROR|Exception"
```

## Stories And Issues

```bash
smith stories get <azdo-remote> <project> <id>
smith stories search <azdo-remote> <project> --query "<text>" [--state <state>] [--type <type>]
smith stories mine <azdo-remote> <project>
smith stories get <github-remote> <repo> <id>
smith stories search <github-remote> <repo> --query "<text>"
smith stories mine <github-remote> <repo>
smith stories get <gitlab-remote> <repo> <id>
smith stories search <gitlab-remote> <repo> --query "<text>"
smith stories mine <gitlab-remote> <repo>
```

## Utilities

```bash
smith code search "<query>" --remote all --format json
smith code grep <github-remote> <repo> "<regex>" --format json
smith code grep <gitlab-remote> <repo> "<regex>" --format json
export GITHUB_GREP_ENABLE_PARALLEL=true
export GITHUB_GREP_MAX_WORKERS=8
smith code grep <github-remote> <repo> "<regex>" --path <path> --glob "<glob>" --branch <branch>
bash scripts/install_claude_commands.sh . copy
```

If rate-limited or slow, lower workers or disable parallel fetch.
