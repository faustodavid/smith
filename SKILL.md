---
name: smith
description: Use when the task is a read-only GitHub, GitLab, Azure DevOps, or YouTrack investigation that needs repo, PR, pipeline, or story evidence. Do not use for write operations, public-web research, or unrelated analysis.
---

# Smith

Run Azure DevOps, GitHub, GitLab, and YouTrack investigations with a deterministic broad-to-narrow workflow and evidence-first outputs.

## Trigger Decision

### Use smith when

- The user asks to search, locate, or grep code or config in GitHub, GitLab, or Azure DevOps.
- The user asks to inspect pull requests, merge requests, review threads, build failures, or pipeline logs.
- The user asks to read work items, GitHub issues, GitLab issues, or YouTrack issues.
- The user asks "Where is X configured?" and the likely source of truth is a repository, PR, pipeline, or story.

### Do not use smith when

- The task requires a write operation.
- The task is public-web or general internet research.
- The task is unrelated writing, brainstorming, or creative work.
- Another tool is clearly the source of truth.

### Ambiguous request fallback

- If GitHub, GitLab, or Azure DevOps is the likely source of truth, start broad with `smith code search "<query>"`.
- If YouTrack is the likely source of truth, start with `smith <youtrack-remote-name> stories search --query "<stable noun>"`.
- For unfamiliar repos, prefer `smith code search "<stable noun>"` over repo-wide grep.
- If repo or project scope is unknown, use discovery helpers first.
- If the request is still unclear after discovery, return the findings so far and the best next narrowing command.

## Command Vocabulary

Use the current CLI tree only:

- Global:
  - `smith code search "<query>"`
  - `smith config <subcommand>`
  - `smith cache clean [--remote <configured-remote-name>|--remote all]`
- Discovery:
  - `smith <azdo-remote-name> orgs`
  - `smith <github-remote-name> orgs`
  - `smith <azdo-remote-name> repos`
  - `smith <azdo-remote-name> repos <project>`
  - `smith <github-remote-name> repos`
  - `smith <gitlab-remote-name> repos [<group>] [--grep <regex>] [--skip <n>] [--take <n>]`
  - `smith <gitlab-remote-name> groups [--grep <regex>] [--skip <n>] [--take <n>]`
- Code:
  - `smith <azdo-remote-name> code search "<query>" [--project <project>] [--repo <repo>]`
  - `smith <github-remote-name> code search "<query>" [--repo <repo>]`
  - `smith <gitlab-remote-name> code search "<query>" [--repo <group/project>]`
  - `smith <azdo-remote-name> code grep <project> <repo> "<regex>"`
  - `smith <github-remote-name> code grep <repo> "<regex>" [--no-clone]`
  - `smith <gitlab-remote-name> code grep <group/project> "<regex>" [--no-clone]`
- Pull requests:
  - `smith <azdo-remote-name> prs list <project> <repo>`
  - `smith <github-remote-name> prs list <repo>`
  - `smith <gitlab-remote-name> prs list <group/project>`
- Pipelines:
  - `smith <azdo-remote-name> pipelines logs list <project> <id>`
  - `smith <github-remote-name> pipelines logs list <repo> <id>`
  - `smith <gitlab-remote-name> pipelines logs list <group/project> <id>`
- Stories and issues:
  - `smith <azdo-remote-name> stories search <project> --query "<text>"`
  - `smith <github-remote-name> stories search <repo> --query "<text>"`
  - `smith <gitlab-remote-name> stories search <group/project> --query "<text>"`
  - `smith <youtrack-remote-name> stories get <issue-id>`
  - `smith <youtrack-remote-name> stories search --query "<text>" [--state <name>] [--type <name>] [--assigned-to <user>]`
  - `smith <youtrack-remote-name> stories mine [--include-closed]`

Representative command examples:

- `smith code search "grafana loki retention"`
- `smith gitlab-infra groups --grep "^platform" --take 25`
- `smith github-readonly code search "OPENAI_WEBHOOK_SECRET" --repo openai-python`
- `smith github-readonly prs list <repo>`
- `smith gitlab-infra prs list <group/project>`
- `smith github-readonly pipelines logs list <repo> <id>`
- `smith gitlab-infra pipelines logs list <group/project> <id>`
- `smith azdo-main stories search <project> --query "<text>"`
- `smith gitlab-infra stories search <group/project> --query "<text>"`
- `smith youtrack-main stories search --query "patch rollout" --state "In Progress"`
- `smith youtrack-main stories get ID-1545`

Do not invent legacy paths such as `discover`, `organizations`, `work`, `ci`, `board`, `stories ticket`, the removed root command `smith search`, root-first forms like `smith code grep <remote>`, or repo-scoped `code`/`prs`/`pipelines` commands for YouTrack.

## Remote Argument Rules

All provider-specific commands use configured remote names as the leading positional argument. Only `code search`, `config`, and `cache` are global entry points.

- Azure DevOps remotes take `<project>` and, when needed, `<repo>`.
- GitHub remotes take a bare `<repo>` slug without the org prefix, not `org/repo`.
- GitLab remotes take full `group/project` paths.
- YouTrack remotes take issue IDs and search filters only; they do not take project or repo arguments.
- `smith code search` always searches all configured remotes.
- To target one remote, use `smith <remote> code search "<query>"`.
- Global `smith code search` does not accept `--remote`, `--project`, or `--repo`.

Important GitHub repo-shape rule:

- Search results and inline evidence often look like `org/repo:path`.
- GitHub command arguments are still bare `<repo>`.

Correct:

- `smith github-readonly code grep openai-python "OPENAI_WEBHOOK_SECRET"`
- `smith github-readonly prs get openai-python 42`
- `smith github-readonly pipelines logs list openai-python 12345`
- `smith github-readonly stories get openai-python 77`

Wrong:

- `smith github-readonly code grep openai/openai-python "OPENAI_WEBHOOK_SECRET"`
- `smith github-readonly prs get openai/openai-python 42`

Important GitLab repo-shape rule:

- Search results and inline evidence often look like `group/repo:path`.
- GitLab command arguments are always full `group/project` paths.
- Use the exact full path returned by search or discovery, such as `acme/platform/api`.

Correct:

- `smith gitlab-infra code grep acme/platform/api "CI_JOB_TOKEN"`
- `smith gitlab-infra prs get acme/platform/api 42`
- `smith gitlab-infra pipelines logs list acme/platform/api 12345`
- `smith gitlab-infra stories get acme/platform/api 77`

Wrong:

- `smith gitlab-infra code grep api "CI_JOB_TOKEN"`
- `smith gitlab-infra prs get api 42`

Important pipeline ID rule:

- `smith <remote> pipelines logs list ... <id>` takes a pipeline, run, or build ID, not a job or log ID.
- If you only have a specific job or log, find the parent pipeline first, then use `smith <remote> pipelines logs grep ... <pipeline-id> ".*" --log-id <job-or-log-id>`.

## Investigation Algorithm

1. Discover candidate scope cautiously.
   - Do not start with a broad regex over the whole repo unless you already know the subsystem path.
   - For repo-backed providers, start with `smith code search "<stable noun>"` to locate the relevant area.
   - For YouTrack, start with `smith <youtrack-remote-name> stories search --query "<stable noun>"`.
   - Important: If the remote is already known, use `smith <remote> code search "<stable noun>"`.
   - Only add `--project` or `--repo` on remote-scoped `smith <remote> code search`, using the provider-appropriate repo shape.
   - If org, project, or repo scope is unclear, use `smith <remote> orgs`, `smith <remote> repos`, or `smith <gitlab-remote> groups`.
   - GitLab discovery defaults to `--take 50`, supports `--grep` and `--skip`, and caps `--take` at `500`.
2. Map only the relevant subtree.
   - After search reveals a likely area, map only that subtree:
     - Azure DevOps: `smith <azdo-remote-name> code grep <project> <repo> ".*" --path <dir> --output-mode files_with_matches`
     - GitHub: `smith <github-remote-name> code grep <repo> ".*" --path <dir> --output-mode files_with_matches`
     - GitLab: `smith <gitlab-remote-name> code grep <group/project> ".*" --path <dir> --output-mode files_with_matches`
3. Extract proof with focused grep.
   - Narrow in this order: repo -> subsystem path -> glob -> regex -> line window.
   - Use both `--path <dir>` and, when possible, `--glob "*.ext"`:
     - Azure DevOps: `smith <azdo-remote-name> code grep <project> <repo> "<regex>" --output-mode content [--path <path>] [--glob <glob>]`
     - GitHub: `smith <github-remote-name> code grep <repo> "<regex>" --output-mode content [--path <path>] [--glob <glob>]`
     - GitLab: `smith <gitlab-remote-name> code grep <group/project> "<regex>" --output-mode content [--path <path>] [--glob <glob>]`
   - Use `--no-clone` for one-off targeted grep or when scanning many repos once.
   - Keep the default clone-backed path when you expect multiple grep calls in the same repo so the local checkout can be reused.
4. Corroborate only when needed.
   - Use `prs` for review context or changed-file confirmation.
   - Use `pipelines logs` for build failures or runtime evidence. List the pipeline once, pick the relevant job by stage or job name, then grep only that `--log-id`.
   - Use `stories` for work-item or issue context.
5. Report only what the retrieved evidence supports.

## Stories and Image Context

When retrieving a story via `smith <remote> stories get <id>`, extract image URLs from the description/comments, download them to `/tmp` on macOS/Linux or `%TEMP%` on Windows, and read the images for full context before analyzing.

## Query Discipline

- Start code search with plain, high-signal terms.
- Prefer another simple search over one overloaded search string.
- For YouTrack, prefer `--state`, `--type`, and `--assigned-to` over manually encoding the same filters in `--query`.
- Do not assume wildcard qualifier support such as `org:foo/bar-*`.
- When search already returns the exact file you need, move to focused grep instead of dumping the whole repo.

## Stop Conditions

- Stop once exact file or log evidence answers the question.
- Do not keep searching after the candidate set is closed and the requested proof is already captured.
- If another narrowing command could still resolve uncertainty, run it.
- If the request remains unresolved after reasonable narrowing, say `not enough evidence` and provide one next command.

## Failure Handling Flow

- 401 or 403:
  - Check org env vars and auth, then retry once.
- 429:
  - Narrow repo, path, glob, regex, or result count.
- Repository too large for grep:
  - The grep guard triggers when the candidate file set is too large.
  - Use `smith code search "<query>"` to locate the relevant path first.
  - Then rerun `smith <remote> code grep ...` with `--path <dir>` and, when possible, `--glob "*.ext"`.
- Truncation:
  - Narrow `--path` and `--glob`, or page with `--from-line` and `--to-line`.
- Empty results:
  - Broaden the search query or remove strict filters.
- Wrong repository:
  - Remap the repo with `smith code search "<broader query>"`.
  - For GitHub, if a command 404s after using `org/repo`, rerun it with the bare `<repo>` slug.
  - For GitLab, if a command 404s after using a short repo name, rerun it with the full `group/project` path.

Use `references/auth-troubleshooting.md` for env or credential setup and `references/failure-playbook.md` for recovery details.

## Answer Contract

- Findings come first. Keep them concise and evidence-based.
- Cite exact paths in the body when you reference code or logs:
  - Azure DevOps: `project/repository:path`
  - GitHub: `org/repository:path`
  - GitLab: `group/repository:path`
  - YouTrack: issue IDs like `RAD-1055` or the full issue URL
- End with a `Sources` section.
- In `Sources`, use exact provider paths unless the caller explicitly requires `repo:path`.
- Multi-provider answers should be split by provider.
- Unresolved answers must include `not enough evidence` and one next command.

## Preflight And Environment

- Confirm you are using the right Smith config file:
  - `smith config path`
  - `smith config list`
  - `smith config show <github-remote-name>`
- Authenticate when needed with the token env var configured for the target remote:
  - Azure DevOps: export the PAT env var referenced by `<azdo-remote-name>` and run `az login` when required.
  - GitHub: export the token env var referenced by `<github-remote-name>` or `gh auth login`.
  - GitLab: export the token env var referenced by `<gitlab-remote-name>` or `glab auth login`.
  - YouTrack: export the token env var referenced by `<youtrack-remote-name>`.
- Use `SMITH_CONFIG=/path/to/config.yaml` when the target remotes live outside the default config path.

Some benchmark harnesses expose only a GitHub subset such as `code search`, `<github-remote-name> code search`, `<github-remote-name> code grep`, `<github-remote-name> orgs`, and `<github-remote-name> repos`. In that environment, stay within the exposed subset instead of switching tools. The single enabled benchmark remote is often named `github`.
