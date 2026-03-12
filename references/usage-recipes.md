# Usage Recipes

Use these patterns after the trigger is confirmed. If a command fails, open `references/failure-playbook.md`.

## Preflight

```bash
export AZURE_DEVOPS_ORG="<org>"
export GITHUB_ORG="<org>"
az login
```

## Default Loop

1. Discover candidates:
```bash
smith code search "<keywords>" --take 30
```
2. Map structure:
```bash
smith code grep azdo <project> <repo> ".*" --output-mode files_with_matches
smith code grep github <repo> ".*" --output-mode files_with_matches
```
3. Extract proof:
```bash
smith code grep azdo <project> <repo> "<regex>" --path <path> --glob "<glob>" --context-lines 2
smith code grep github <repo> "<regex>" --path <path> --glob "<glob>" --context-lines 2
```
4. Report exact `project/repository:path` or `org/repository:path` sources. If unresolved, say `not enough evidence` and give one next command.

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
smith pipelines logs list azdo <project> <build_id>
smith pipelines logs grep azdo <project> <build_id> "ERROR|Exception" --output-mode logs_with_matches
smith pipelines logs grep azdo <project> <build_id> ".*" --log-id <log_id> --from-line <n>
smith pipelines logs list github <repo> <run_id>
smith pipelines logs grep github <repo> <run_id> "ERROR|Exception"
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
