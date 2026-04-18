# Usage Recipes

Full command surface and per-provider examples. Read this when you need a flag or a less-common command. For trigger and loop, see `SKILL.md`.

## Preflight

```bash
smith config path
smith config list
smith config show <remote-name>
```

If the config lives elsewhere, set `SMITH_CONFIG=/path/to/config.yaml`. Authenticate with the `token_env` declared on the remote (common: `GITHUB_TOKEN`, `GITLAB_TOKEN`, `AZURE_DEVOPS_PAT`). For Azure DevOps also run `az login`.

## Discovery

```bash
smith <azdo-remote-name> orgs
smith <github-remote-name> orgs
smith <gitlab-remote-name> groups --grep "^platform" --take 25
smith <azdo-remote-name> repos
smith <azdo-remote-name> repos <project>
smith <github-remote-name> repos
smith <gitlab-remote-name> repos --grep "^platform/" --take 25
smith <gitlab-remote-name> repos <group> --skip 25 --take 25
```

GitLab `repos`/`groups` default to `--take 50`, support `--grep`/`--skip`, cap at `--take 500`, and surface a truncation notice when results overflow.

## Code Search

```bash
smith code search "<query>" --take 30
smith <azdo-remote-name> code search "<query>" --project <project> --repo <repo>
smith <github-remote-name> code search "<query>" --repo <repo>
smith <gitlab-remote-name> code search "<query>" --repo <group/project>
```

Global `smith code search` targets every enabled remote and rejects `--project` or `--repo`.

## Code Grep

```bash
smith <azdo-remote-name> code grep <project> <repo> ".*" --output-mode files_with_matches --path <dir>
smith <github-remote-name> code grep <repo> ".*" --output-mode files_with_matches --path <dir>
smith <gitlab-remote-name> code grep <group/project> ".*" --output-mode files_with_matches --path <dir>

smith <azdo-remote-name> code grep <project> <repo> "<regex>" --path <path> --glob "<glob>" --context-lines 2
smith <github-remote-name> code grep <repo> "<regex>" --path <path> --glob "<glob>" --context-lines 2
smith <gitlab-remote-name> code grep <group/project> "<regex>" --path <path> --glob "<glob>" --context-lines 2
```

All grep variants also accept `--branch`, `--from-line`/`--to-line`, `--reverse`, `--case-sensitive`, and `--no-clone` (skip local clone and fetch via provider APIs — good for one-off scans across many repos). `--output-mode` is one of `content` (default), `files_with_matches`, or `count`.

## Pull Requests / Merge Requests

```bash
smith prs search "<query>" --status active --exclude-drafts
smith <azdo-remote-name> prs search "<query>" --project <project> --repo <repo> --status active
smith <github-remote-name> prs search "<query>" --repo <repo> --creator <user>
smith <gitlab-remote-name> prs search "<query>" --repo <group/project> --date-from 2025-01-01

smith <azdo-remote-name> prs list <project> <repo> --status active,completed --take 25
smith <github-remote-name> prs list <repo> --status active,completed
smith <gitlab-remote-name> prs list <group/project> --status active,completed

smith <azdo-remote-name> prs get <project> <repo> <id>
smith <github-remote-name> prs get <repo> <id>
smith <gitlab-remote-name> prs get <group/project> <id>

smith <azdo-remote-name> prs threads <project> <repo> <id>
smith <github-remote-name> prs threads <repo> <id>
smith <gitlab-remote-name> prs threads <group/project> <id>
```

`prs search`/`prs list` accept `--status active|completed|abandoned`, `--creator`, `--date-from`, `--date-to`, `--skip`, `--take`, `--exclude-drafts`, and `--include-labels`.

## Pipeline Logs

```bash
smith <azdo-remote-name> pipelines logs list <project> <id>
smith <github-remote-name> pipelines logs list <repo> <id>
smith <gitlab-remote-name> pipelines logs list <group/project> <id>

smith <azdo-remote-name> pipelines logs grep <project> <id> "ERROR|Exception" --output-mode logs_with_matches
smith <github-remote-name> pipelines logs grep <repo> <id> "ERROR|Exception"
smith <gitlab-remote-name> pipelines logs grep <group/project> <id> "ERROR|Exception"
smith <azdo-remote-name> pipelines logs grep <project> <id> ".*" --log-id <log_id> --from-line <n>

# Error analysis: show the newest hits first so truncation drops older ones.
smith <github-remote-name> pipelines logs grep <repo> <id> "error|Exception|Traceback" --reverse
```

`<id>` is the pipeline, run, or build ID — not a job or log ID. To drill into one job, find the pipeline ID first, then use `--log-id <job-or-log-id>` on `pipelines logs grep`.

## Stories / Issues

```bash
smith <azdo-remote-name> stories get <project> <id>
smith <github-remote-name> stories get <repo> <id>
smith <gitlab-remote-name> stories get <group/project> <id>
smith <youtrack-remote-name> stories get <issue-id>

smith <azdo-remote-name> stories search <project> --query "<text>" --state <state> --type <type>
smith <github-remote-name> stories search <repo> --query "<text>"
smith <gitlab-remote-name> stories search <group/project> --query "<text>"
smith <youtrack-remote-name> stories search --query "<text>" --state <name> --type <name> --assigned-to <user>

smith <azdo-remote-name> stories mine <project>
smith <github-remote-name> stories mine <repo>
smith <gitlab-remote-name> stories mine <group/project>
smith <youtrack-remote-name> stories mine --include-closed
```

`stories search` accepts `--area` (not on YouTrack), `--type`, `--state`, `--assigned-to`, `--skip`, `--take`.

## Output, Parallelism, and Cache

```bash
smith code search "<query>" --format json
smith <github-remote-name> code grep <repo> "<regex>" --format json
smith cache clean --remote <remote-name>   # or --remote all

export GITHUB_GREP_ENABLE_PARALLEL=true
export GITHUB_GREP_MAX_WORKERS=8
```

If rate-limited or slow, lower `GITHUB_GREP_MAX_WORKERS` or unset `GITHUB_GREP_ENABLE_PARALLEL`.
