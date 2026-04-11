# Usage Recipes

Use these patterns after the trigger is confirmed. If a command fails, open `references/failure-playbook.md`.

## Preflight

```bash
smith config path
smith config list
smith config show <github-remote-name>
az login
gh auth status
glab auth status
```

If the target remotes live in a non-default config file, run commands with `SMITH_CONFIG=/path/to/config.yaml`.

Authenticate with the token env var configured on the target remote. Common examples include `AZURE_DEVOPS_PAT`, `GITHUB_TOKEN`, and `GITLAB_TOKEN`.

GitHub repo-shape rule:

- Search results look like `org/repo:path`.
- GitHub command arguments use bare `<repo>`, not `org/repo`.
- Example: `smith code grep <github-remote-name> openai-python "OPENAI_WEBHOOK_SECRET"`

GitLab repo-shape rule:

- Search results look like `group/repo:path`.
- GitLab command arguments use `<repo>` relative to the configured remote's group, not full `group/repo`.
- Example: `smith code grep <gitlab-remote-name> api "CI_JOB_TOKEN"`

## Default Loop

1. Discover candidates:
```bash
smith code search "<keywords>" --take 30
```
2. Map structure only when repo is known but file scope is not:
```bash
smith code grep <azdo-remote-name> <project> <repo> ".*" --output-mode files_with_matches
smith code grep <github-remote-name> <repo> ".*" --output-mode files_with_matches
smith code grep <gitlab-remote-name> <repo> ".*" --output-mode files_with_matches
```
3. Extract proof from the smallest useful scope:
```bash
smith code grep <azdo-remote-name> <project> <repo> "<regex>" --path <path> --glob "<glob>" --context-lines 2
smith code grep <github-remote-name> <repo> "<regex>" --path <path> --glob "<glob>" --context-lines 2
smith code grep <gitlab-remote-name> <repo> "<regex>" --path <path> --glob "<glob>" --context-lines 2
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
smith orgs <azdo-remote-name>
smith orgs <github-remote-name>
smith orgs <gitlab-remote-name>
smith repos <azdo-remote-name>
smith repos <azdo-remote-name> <project>
smith repos <github-remote-name>
smith repos <gitlab-remote-name>
```

## Pull Requests

```bash
smith prs list <azdo-remote-name> <project> <repo> --status active,completed --take 25
smith prs get <azdo-remote-name> <project> <repo> <id>
smith prs threads <azdo-remote-name> <project> <repo> <id>
smith prs list <github-remote-name> <repo> --status active,completed
smith prs get <github-remote-name> <repo> <id>
smith prs threads <github-remote-name> <repo> <id>
smith prs list <gitlab-remote-name> <repo> --status active,completed
smith prs get <gitlab-remote-name> <repo> <id>
smith prs threads <gitlab-remote-name> <repo> <id>
```

## Pipeline Logs

```bash
smith pipelines logs list <azdo-remote-name> <project> <id>
smith pipelines logs grep <azdo-remote-name> <project> <id> "ERROR|Exception" --output-mode logs_with_matches
smith pipelines logs grep <azdo-remote-name> <project> <id> ".*" --log-id <log_id> --from-line <n>
smith pipelines logs list <github-remote-name> <repo> <id>
smith pipelines logs grep <github-remote-name> <repo> <id> "ERROR|Exception"
smith pipelines logs list <gitlab-remote-name> <repo> <id>
smith pipelines logs grep <gitlab-remote-name> <repo> <id> "ERROR|Exception"
```

## Stories And Issues

```bash
smith stories get <azdo-remote-name> <project> <id>
smith stories search <azdo-remote-name> <project> --query "<text>" [--state <state>] [--type <type>]
smith stories mine <azdo-remote-name> <project>
smith stories get <github-remote-name> <repo> <id>
smith stories search <github-remote-name> <repo> --query "<text>"
smith stories mine <github-remote-name> <repo>
smith stories get <gitlab-remote-name> <repo> <id>
smith stories search <gitlab-remote-name> <repo> --query "<text>"
smith stories mine <gitlab-remote-name> <repo>
```

## Utilities

```bash
smith code search "<query>" --remote all --format json
smith code grep <github-remote-name> <repo> "<regex>" --format json
smith code grep <gitlab-remote-name> <repo> "<regex>" --format json
export GITHUB_GREP_ENABLE_PARALLEL=true
export GITHUB_GREP_MAX_WORKERS=8
smith code grep <github-remote-name> <repo> "<regex>" --path <path> --glob "<glob>" --branch <branch>
bash scripts/install_claude_commands.sh . copy
```

If rate-limited or slow, lower workers or disable parallel fetch.
