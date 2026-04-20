---
name: smith
description: Use when the user asks to read, search, or investigate code, configs, pull/merge requests, pipeline logs, work items, or issues on GitHub, GitLab, Azure DevOps, or YouTrack. Make sure to invoke this skill for any read-only source-of-truth lookup in those systems, including ambiguous "where is X configured?" or "why did this build fail?" questions. Do not use for write operations, commenting, approving, or general internet research.
---

# Smith

Read-only, evidence-first investigations across GitHub, GitLab, Azure DevOps, and YouTrack.

## Trigger Decision

### Use smith when

- The request is read-only and the source of truth is GitHub, GitLab, Azure DevOps, or YouTrack.
- Search, locate, or grep code or config.
- Inspect a pull/merge request, review threads, or changed files.
- Investigate a pipeline error or build failure.
- Read or search work items or issues.

### Do not use smith when

- The request is a write operation: create, update, approve, comment, or post.
- The source of truth is the public internet.

### Ambiguous request fallback

- Default to `smith code search "<stable noun>"`.
- YouTrack-only context: `smith <youtrack-remote-name> stories search --query "<text>"`.
- If scope is unknown, discover first (see `references/usage-recipes.md`).
- If still unclear after discovery, return findings so far plus the best next narrowing command.

## Command Map

Full vocabulary and flags live in `references/usage-recipes.md`. The minimum you need:

| Scope | Command form |
|---|---|
| Cross-remote search | `smith code search "<query>"`, `smith prs search "<query>"` |
| Single remote search | `smith <azdo-remote-name> code search`, `smith <github-remote-name> code search`, `smith <gitlab-remote-name> code search` |
| Discovery | `smith <azdo-remote-name> orgs`, `smith <github-remote-name> orgs`, `smith <gitlab-remote-name> groups`, `smith <azdo-remote-name> repos <project>`, `smith <github-remote-name> repos`, `smith <gitlab-remote-name> repos` |
| Focused grep | `smith <azdo-remote-name> code grep <project> <repo> "<regex>"`, `smith <github-remote-name> code grep <repo> "<regex>"`, `smith <gitlab-remote-name> code grep <group/project> "<regex>"` |
| PRs / MRs | `smith <azdo-remote-name> prs search`, `smith <github-remote-name> prs search`, `smith <gitlab-remote-name> prs search`, `smith <github-remote-name> prs list <repo>`, `smith <gitlab-remote-name> prs list <group/project>` |
| Pipelines | `smith <github-remote-name> pipelines list <repo> <id>`, `smith <gitlab-remote-name> pipelines list <group/project> <id>`, `smith <github-remote-name> pipelines logs list <repo> <id>`, `smith <gitlab-remote-name> pipelines logs list <group/project> <id>` |
| Stories / Issues | `smith <azdo-remote-name> stories search <project> --query`, `smith <gitlab-remote-name> stories search <group/project> --query`, `smith <youtrack-remote-name> stories search --query` |

Rules that save retries:

- **GitHub**: repo arg is bare `<repo>`, not `org/repo`. Search output may look like `org/repo:path` but commands still take `<repo>`.
- **GitLab**: repo arg uses full `group/project` paths, not a short repo name.
- **Azure DevOps**: two positional args, `<project> <repo>`.
- **YouTrack**: no repo arg; only issue IDs (e.g. `RAD-1055`) and `--query`.
- Global `smith code search` and `smith prs search` target every enabled remote and reject `--project` or `--repo`. Use `smith <remote> ...` to narrow.
- `pipelines logs list ... <id>` expects a pipeline/run/build ID. For a specific job or log, find the parent ID first, then `pipelines logs grep ... <pipeline-id> ".*" --log-id <job-or-log-id>`.
- `pipelines list ... <id>` prints a compact DAG (`@` pipelines, `#` stages, `*` jobs, inline `<needs` and `>>` downstream). GitLab traverses child pipelines via GraphQL (REST fallback emits header-only rows with a warning). Filter with `--status`, `--grep`, `--skip`/`--take`, `--max-depth` (gitlab only, default 0 = unlimited). Full grammar lives in `references/pipelines-format.md`.

Use `--help` on any command for flags.

## Investigation Algorithm

1. **List remotes** with `smith config list` so you know what's configured.
2. **Go broad** with `smith code search "<stable noun>"` (all remotes) or `smith <remote> code search` when the remote is known. For YouTrack, start with `stories search`. If org, project, or repo scope is unknown, use discovery first.
3. **Map the subtree** with `smith <remote> code grep <scope> ".*" --output-mode files_with_matches --path <dir>` before running wider regex.
4. **Extract proof** with focused grep. Narrow in this order: repo → `--path` → `--glob` → regex → `--from-line`/`--to-line`. Use `--no-clone` for one-off scans across many repos; keep the default clone path when you expect multiple greps in the same repo so the checkout can be reused.
5. **Corroborate only when needed**: `prs` for review context, `pipelines logs` for build evidence (list once, pick the relevant job by stage/name, then grep only that `--log-id`; for error log analysis prefer `--reverse` so the latest hits survive truncation), `stories` for work-item context. When a story has images, download attachments to `/tmp` (macOS/Linux) or `%TEMP%` (Windows) and read them before drawing conclusions.
6. **Report** only what the retrieved evidence supports and cite `URL`.

### Pipeline Analysis
1. Use `smith pipelines list <repo> <pipeline_id> --status failed` to focus on failed jobs.
2. Once you know the pipeline log ID, use `smith pipelines logs grep <repo> <pipeline_id> <log_id> --reverse` to analyze the logs.
## Stop Conditions

Stop narrowing and answer when any of these is true:

- The required `<scope>:<path>` evidence is in hand and sufficient for the question.
- Every reasonable narrowing path has been tried and returned empty — respond with `not enough evidence` and one next command.
- A recovery loop for `401 or 403`, `429`, `Truncation`, `Empty results`, or `Wrong repository` has already retried once without progress — surface the blocker (see Failure Handling Flow).

## Failure Handling Flow

Use `references/failure-playbook.md` for the full matrix and `references/auth-troubleshooting.md` for env/credential steps. In short:

- **401 or 403** — confirm the remote is in the active config, confirm the token env var is set, run `az login` / `gh auth login` / `glab auth login`, retry once.
- **429** — lower `--take` and narrow `--path`/`--glob`/regex; for GitHub grep, reduce `GITHUB_GREP_MAX_WORKERS` or unset `GITHUB_GREP_ENABLE_PARALLEL`.
- **Truncation** — narrow `--path` and `--glob`, page with `--from-line`/`--to-line`, reduce `--context-lines`.
- **Empty results** — broaden the `code search` query, drop strict filters, rerun focused grep on the candidate repo.
- **Wrong repository** — rerun `smith code search "<broader>"`; remember GitHub wants bare `<repo>`, GitLab wants full `group/project`.

## Answer Contract

- Findings first, concise and evidence-based. Every claim cites `<project>/<repository>:<path>`, `<org>/<repository>:<path>`, or `<group>/<repository>:<path>` (or just `repo:path` if the caller explicitly asks for that contract).
- Single-remote answers stay flat. Multi-remote answers split by remote.
- Unresolved answers include `not enough evidence` and one next command.
- Always end with a `Sources` section of absolute URLs.

### Sources URL templates

Build URLs from the remote's `host` and `org` (see `smith config show <remote>`). Default hosts: `github.com`, `gitlab.com`, `dev.azure.com`. `HEAD` resolves to the current default branch at click time; swap in a branch or commit SHA if you need a permalink. Anchor multi-line ranges to the primary match line. For Azure DevOps legacy hosts (e.g. `<org>.visualstudio.com`), use the remote's `host` in place of `dev.azure.com/<org>`.

File / blob:

- GitHub: `https://<host>/<org>/<repo>/blob/HEAD/<path>#L<line>`
- GitLab: `https://<host>/<group>/<project>/-/blob/HEAD/<path>#L<line>`
- Azure DevOps: `https://dev.azure.com/<org>/<project>/_git/<repo>?path=/<path>&line=<line>&_a=contents`

Pull / merge request:

- GitHub: `https://<host>/<org>/<repo>/pull/<id>`
- GitLab: `https://<host>/<group>/<project>/-/merge_requests/<id>`
- Azure DevOps: `https://dev.azure.com/<org>/<project>/_git/<repo>/pullrequest/<id>`

Pipeline / run:

- GitHub: `https://<host>/<org>/<repo>/actions/runs/<id>`
- GitLab: `https://<host>/<group>/<project>/-/pipelines/<id>`
- Azure DevOps: `https://dev.azure.com/<org>/<project>/_build/results?buildId=<id>`

Story / issue / work item:

- GitHub: `https://<host>/<org>/<repo>/issues/<id>`
- GitLab: `https://<host>/<group>/<project>/-/issues/<id>`
- Azure DevOps: `https://dev.azure.com/<org>/<project>/_workitems/edit/<id>`
- YouTrack: `https://<host>/issue/<id>`
