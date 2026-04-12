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
- Example: `smith <github-remote-name> code grep openai-python "OPENAI_WEBHOOK_SECRET"`

GitLab repo-shape rule:

- Search results look like `group/repo:path`.
- GitLab command arguments use full `group/project` paths.
- Example: `smith <gitlab-remote-name> code grep acme/platform/api "CI_JOB_TOKEN"`

## Default Loop

1. Discover candidates:
```bash
smith code search "<keywords>" --take 30
```
2. Map structure only when repo is known but file scope is not:
```bash
smith <azdo-remote-name> code grep <project> <repo> ".*" --output-mode files_with_matches
smith <github-remote-name> code grep <repo> ".*" --output-mode files_with_matches
smith <gitlab-remote-name> code grep <group/project> ".*" --output-mode files_with_matches
```
3. Extract proof from the smallest useful scope:
```bash
smith <azdo-remote-name> code grep <project> <repo> "<regex>" --path <path> --glob "<glob>" --context-lines 2
smith <github-remote-name> code grep <repo> "<regex>" --path <path> --glob "<glob>" --context-lines 2
smith <gitlab-remote-name> code grep <group/project> "<regex>" --path <path> --glob "<glob>" --context-lines 2
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
smith <azdo-remote-name> orgs
smith <github-remote-name> orgs
smith <gitlab-remote-name> orgs
smith <azdo-remote-name> repos
smith <azdo-remote-name> repos <project>
smith <github-remote-name> repos
smith <gitlab-remote-name> repos
smith <gitlab-remote-name> groups list
```

## Pull Requests

```bash
smith <azdo-remote-name> prs list <project> <repo> --status active,completed --take 25
smith <azdo-remote-name> prs get <project> <repo> <id>
smith <azdo-remote-name> prs threads <project> <repo> <id>
smith <github-remote-name> prs list <repo> --status active,completed
smith <github-remote-name> prs get <repo> <id>
smith <github-remote-name> prs threads <repo> <id>
smith <gitlab-remote-name> prs list <group/project> --status active,completed
smith <gitlab-remote-name> prs get <group/project> <id>
smith <gitlab-remote-name> prs threads <group/project> <id>
```

## Pipeline Logs

```bash
smith <azdo-remote-name> pipelines logs list <project> <id>
smith <azdo-remote-name> pipelines logs grep <project> <id> "ERROR|Exception" --output-mode logs_with_matches
smith <azdo-remote-name> pipelines logs grep <project> <id> ".*" --log-id <log_id> --from-line <n>
smith <github-remote-name> pipelines logs list <repo> <id>
smith <github-remote-name> pipelines logs grep <repo> <id> "ERROR|Exception"
smith <gitlab-remote-name> pipelines logs list <group/project> <id>
smith <gitlab-remote-name> pipelines logs grep <group/project> <id> "ERROR|Exception"
```

## Stories And Issues

```bash
smith <azdo-remote-name> stories get <project> <id>
smith <azdo-remote-name> stories search <project> --query "<text>" [--state <state>] [--type <type>]
smith <azdo-remote-name> stories mine <project>
smith <github-remote-name> stories get <repo> <id>
smith <github-remote-name> stories search <repo> --query "<text>"
smith <github-remote-name> stories mine <repo>
smith <gitlab-remote-name> stories get <group/project> <id>
smith <gitlab-remote-name> stories search <group/project> --query "<text>"
smith <gitlab-remote-name> stories mine <group/project>
```

## Utilities

```bash
smith code search "<query>" --format json
smith <github-remote-name> code search "<query>" --repo <repo> --format json
smith <github-remote-name> code grep <repo> "<regex>" --format json
smith <gitlab-remote-name> code grep <group/project> "<regex>" --format json
export GITHUB_GREP_ENABLE_PARALLEL=true
export GITHUB_GREP_MAX_WORKERS=8
smith <github-remote-name> code grep <repo> "<regex>" --path <path> --glob "<glob>" --branch <branch>
bash scripts/install_claude_commands.sh . copy
```

If rate-limited or slow, lower workers or disable parallel fetch.
