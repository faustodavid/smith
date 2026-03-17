# Usage Recipes

Use these patterns after the trigger is confirmed. If a command fails, open `references/failure-playbook.md`.

## Preflight

```bash
export AZURE_DEVOPS_ORG="<org>"
export GITHUB_ORG="<org>"
az login
gh auth status
```

GitHub repo-shape rule:

- Search results look like `org/repo:path`.
- GitHub command arguments use bare `<repo>`, not `org/repo`.
- Example: `smith code grep github openai-python "OPENAI_WEBHOOK_SECRET"`

## Default Loop

1. Discover candidates:
```bash
smith code search "<keywords>" --take 30
```
2. Map structure only when repo is known but file scope is not:
```bash
smith code grep azdo <project> <repo> ".*" --output-mode files_with_matches
smith code grep github <repo> ".*" --output-mode files_with_matches
```
3. Extract proof from the smallest useful scope:
```bash
smith code grep azdo <project> <repo> "<regex>" --path <path> --glob "<glob>" --context-lines 2
smith code grep github <repo> "<regex>" --path <path> --glob "<glob>" --context-lines 2
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
smith orgs azdo
smith orgs github
smith repos azdo
smith repos azdo <project>
smith repos github
```

## Pull Requests

```bash
smith prs list azdo <project> <repo> --status active,completed --take 25
smith prs get azdo <project> <repo> <id>
smith prs threads azdo <project> <repo> <id>
smith prs list github <repo> --status active,completed
smith prs get github <repo> <id>
smith prs threads github <repo> <id>
```

## Pipeline Logs

```bash
smith pipelines logs list azdo <project> <id>
smith pipelines logs grep azdo <project> <id> "ERROR|Exception" --output-mode logs_with_matches
smith pipelines logs grep azdo <project> <id> ".*" --log-id <log_id> --from-line <n>
smith pipelines logs list github <repo> <id>
smith pipelines logs grep github <repo> <id> "ERROR|Exception"
```

## Stories And Issues

```bash
smith stories get azdo <project> <id>
smith stories search azdo <project> --query "<text>" [--state <state>] [--type <type>]
smith stories mine azdo <project>
smith stories get github <repo> <id>
smith stories search github <repo> --query "<text>"
smith stories mine github <repo>
```

## Utilities

```bash
smith code search "<query>" --format json
smith code grep github <repo> "<regex>" --format json
export GITHUB_GREP_ENABLE_PARALLEL=true
export GITHUB_GREP_MAX_WORKERS=8
smith code grep github <repo> "<regex>" --path <path> --glob "<glob>" --branch <branch>
bash scripts/install_claude_commands.sh . copy
```

If rate-limited or slow, lower workers or disable parallel fetch.
