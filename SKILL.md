---
name: smith
description: Use when the task is a read-only GitHub, GitLab, or Azure DevOps investigation that needs repo, PR, pipeline, or story evidence. Do not use for write operations, public-web research, or unrelated analysis.
---

# Smith

Run Azure DevOps, GitHub, and GitLab investigations with a deterministic broad-to-narrow workflow and evidence-first outputs.

## Trigger Decision

### Use smith when

- The user asks to search, locate, or grep code or config in GitHub, GitLab, or Azure DevOps.
- The user asks to inspect pull requests, merge requests, review threads, build failures, or pipeline logs.
- The user asks to read work items, GitHub issues, or GitLab issues.
- The user asks "Where is X configured?" and the likely source of truth is a repository, PR, pipeline, or story.

### Do not use smith when

- The task requires a write operation.
- The task is public-web or general internet research.
- The task is unrelated writing, brainstorming, or creative work.
- Another tool is clearly the source of truth.

### Ambiguous request fallback

- If GitHub, GitLab, or Azure DevOps is the likely source of truth, start broad with `smith code search "<query>"`.
- For unfamiliar repos, prefer `smith code search "<stable noun>"` over repo-wide grep.
- If repo or project scope is unknown, use discovery helpers first.
- If the request is still unclear after discovery, return the findings so far and the best next narrowing command.

## Command Vocabulary

Use the current CLI tree only:

- Discovery:
  - `smith orgs <remote>`
  - `smith repos <azdo-remote> [<project>]`
  - `smith repos <github-remote>`
  - `smith repos <gitlab-remote>`
- Code:
  - `smith code search "<query>" [--remote <remote>|--remote all]`
  - `smith code grep <azdo-remote> <project> <repo> "<regex>"`
  - `smith code grep <github-remote> <repo> "<regex>" [--no-clone]`
  - `smith code grep <gitlab-remote> <repo> "<regex>" [--no-clone]`
- Pull requests:
  - `smith prs list|get|threads <remote> ...`
- Pipelines:
  - `smith pipelines logs list|grep <remote> ...`
- Stories and issues:
  - `smith stories get|search|mine <remote> ...`

Representative command examples:

- `smith prs list github-readonly <repo>`
- `smith prs list gitlab-infra <repo>`
- `smith pipelines logs list github-readonly <repo> <id>`
- `smith pipelines logs list gitlab-infra <repo> <id>`
- `smith stories search azdo-main <project> --query "<text>"`
- `smith stories search gitlab-infra <repo> --query "<text>"`

Do not invent legacy paths such as `discover`, `organizations`, `work`, `ci`, `board`, or `stories ticket`.

## Remote Argument Rules

`code grep`, `prs`, `pipelines logs`, and `stories` use configured remote names as the leading positional argument.

- Azure DevOps remotes take `<project>` and, when needed, `<repo>`.
- GitHub remotes take a bare `<repo>` slug without the org prefix, not `org/repo`.
- GitLab remotes take `<repo>` relative to the remote's configured group, not full `group/repo`.

Important GitHub repo-shape rule:

- Search results and inline evidence often look like `org/repo:path`.
- GitHub command arguments are still bare `<repo>`.

Correct:

- `smith code grep github-readonly openai-python "OPENAI_WEBHOOK_SECRET"`
- `smith prs get github-readonly openai-python 42`
- `smith pipelines logs list github-readonly openai-python 12345`
- `smith stories get github-readonly openai-python 77`

Wrong:

- `smith code grep github-readonly openai/openai-python "OPENAI_WEBHOOK_SECRET"`
- `smith prs get github-readonly openai/openai-python 42`

Important GitLab repo-shape rule:

- Search results and inline evidence often look like `group/repo:path`.
- GitLab command arguments are `<repo>` relative to configured `GITLAB_GROUP`.
- If `GITLAB_GROUP=acme/platform`, use `api` or `services/api`, not full `group/repo`.

Correct:

- `smith code grep gitlab-infra api "CI_JOB_TOKEN"`
- `smith prs get gitlab-infra services/api 42`
- `smith pipelines logs list gitlab-infra api 12345`
- `smith stories get gitlab-infra api 77`

Wrong:

- `smith code grep gitlab-infra acme/platform/api "CI_JOB_TOKEN"`
- `smith prs get gitlab-infra acme/platform/api 42`

Important pipeline ID rule:

- `smith pipelines logs list ... <id>` takes a pipeline, run, or build ID, not a job or log ID.
- If you only have a specific job or log, find the parent pipeline first, then use `smith pipelines logs grep ... <pipeline-id> ".*" --log-id <job-or-log-id>`.

## Investigation Algorithm

1. Confirm the request is read-only and GitHub, GitLab, or Azure DevOps backed.
2. Discover candidate scope cautiously.
   - Do not start with a broad regex over the whole repo unless you already know the subsystem path.
   - Start with `smith code search "<stable noun>"` to locate the relevant area. Include `--repo <repo>` if the repository is already known.
   - If org, project, or repo scope is unclear, use `smith orgs ...` or `smith repos ...`.
3. Map only the relevant subtree.
   - After search reveals a likely area, map only that subtree:
     - Azure DevOps: `smith code grep <azdo-remote> <project> <repo> ".*" --path <dir> --output-mode files_with_matches`
     - GitHub: `smith code grep <github-remote> <repo> ".*" --path <dir> --output-mode files_with_matches`
     - GitLab: `smith code grep <gitlab-remote> <repo> ".*" --path <dir> --output-mode files_with_matches`
4. Extract proof with focused grep.
   - Narrow in this order: repo -> subsystem path -> glob -> regex -> line window.
   - Use both `--path <dir>` and, when possible, `--glob "*.ext"`:
     - Azure DevOps: `smith code grep <azdo-remote> <project> <repo> "<regex>" --output-mode content [--path <path>] [--glob <glob>]`
     - GitHub: `smith code grep <github-remote> <repo> "<regex>" --output-mode content [--path <path>] [--glob <glob>]`
     - GitLab: `smith code grep <gitlab-remote> <repo> "<regex>" --output-mode content [--path <path>] [--glob <glob>]`
   - Use `--no-clone` for one-off targeted grep or when scanning many repos once.
   - Keep the default clone-backed path when you expect multiple grep calls in the same repo so the local checkout can be reused.
5. Corroborate only when needed.
   - Use `prs` for review context or changed-file confirmation.
   - Use `pipelines logs` for build failures or runtime evidence. List the pipeline once, pick the relevant job by stage or job name, then grep only that `--log-id`.
   - Use `stories` for work-item or issue context.
6. Report only what the retrieved evidence supports.

## Query Discipline

- Start code search with plain, high-signal terms.
- Prefer another simple search over one overloaded search string.
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
  - Then rerun `smith code grep ...` with `--path <dir>` and, when possible, `--glob "*.ext"`.
- Truncation:
  - Narrow `--path` and `--glob`, or page with `--from-line` and `--to-line`.
- Empty results:
  - Broaden the search query or remove strict filters.
- Wrong repository:
  - Remap the repo with `smith code search "<broader query>"`.
  - For GitHub, if a command 404s after using `org/repo`, rerun it with the bare `<repo>` slug.
  - For GitLab, if a command 404s after using full `group/repo`, rerun it with the repo path relative to configured `GITLAB_GROUP`.

Use `references/auth-troubleshooting.md` for env or credential setup and `references/failure-playbook.md` for recovery details.

## Answer Contract

- Findings come first. Keep them concise and evidence-based.
- Cite exact paths in the body when you reference code or logs:
  - Azure DevOps: `project/repository:path`
  - GitHub: `org/repository:path`
  - GitLab: `group/repository:path`
- End with a `Sources` section.
- In `Sources`, use exact provider paths unless the caller explicitly requires `repo:path`.
- Multi-provider answers should be split by provider.
- Unresolved answers must include `not enough evidence` and one next command.

## Preflight And Environment

- Set org context before running provider commands:
  - `export AZURE_DEVOPS_ORG="<org>"`
  - `export GITHUB_ORG="<org>"`
  - `export GITLAB_GROUP="<group>"`
- Authenticate when needed:
  - Azure DevOps: `az login`
  - GitHub: `export GITHUB_TOKEN="<token>"` or `gh auth login`
  - GitLab: `export GITLAB_TOKEN="<token>"` or `glab auth login`
- Per-invocation overrides are available:
  - `smith --azdo-org <org> ...`
  - `smith --github-org <org> ...`
  - `smith --gitlab-group <group> ...`

Some benchmark harnesses expose only a GitHub subset such as `code search`, `code grep github`, `orgs github`, and `repos github`. In that environment, stay within the exposed subset instead of switching tools.
