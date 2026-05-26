---
name: smith
description: Use when the user asks to read, search, grep, or investigate code/config across GitHub, GitLab, Azure DevOps, and YouTrack. Use especially for remote cross-repo code search, PRs/MRs, pipelines, and issues.
---

# Smith

Read-only, evidence-first investigations across GitHub, GitLab, Azure DevOps, and YouTrack.

## Trigger Decision

### Use smith when

- Search, locate, or grep code or config across remotes.
- Inspect PRs/MRs, review threads, or changed files.
- Investigate pipeline errors or build failures.
- Read or search work items, stories, or issues.

### Do not use smith when

- The request is a write operation (create, update, approve, comment, post).
- The source of truth is the public internet.

### Ambiguous request fallback

- Default to `smith code search "<stable noun>"`.
- YouTrack: `smith <youtrack-remote> stories search --query "<text>"`.
- Unknown scope: discover first (see `references/usage-recipes.md`).
- Still unclear: return findings plus the best narrowing command.

## Command Map

Full vocabulary and flags in `references/usage-recipes.md`.

| Scope | Command form |
|---|---|
| Cross-remote search | `smith code search "<query>" [--glob "<glob>"]`, `smith prs search "<query>"` |
| Single remote search | `smith <remote> code search [--glob "<glob>"]` |
| Discovery | `smith <remote> orgs`, `smith <remote> repos [scope]`, `smith <remote> groups` (GitLab) |
| Focused grep | `smith <remote> code grep <scope> "<regex>"` |
| PRs / MRs | `smith <remote> prs search`, `smith <remote> prs list <scope>`, `smith <remote> prs get <scope> <id>` |
| Pipelines | `smith <remote> pipelines list <scope> <id>`, `smith <remote> pipelines grep <scope> <id> "<regex>"` |
| Pipeline artifacts | `smith <remote> pipelines artifacts list\|grep <group/project> <pipeline-id> <job-id>` (GitLab only) |
| Stories / Issues | `smith <remote> stories search [scope] --query` |

### Key flags

- `--glob` on code search filters result paths by file type/pattern. Use early when the question implies file types.
- Code/pipeline grep: `--path`, `--glob`, `--output-mode` (content/files_with_matches/count), `--context-lines`, `--from-line`/`--to-line`, `--reverse`, `--case-sensitive`.
- Code grep adds: `--branch`.
- Pipeline grep adds: `--log-id`.

### Repo argument rules

- **GitHub**: bare `<repo>`, not `org/repo`.
- **GitLab**: full `group/project` path. `code search` and `prs search` exclude personal projects by default; add `--include-personal` when needed.
- **Azure DevOps**: `<project> <repo>`.
- **YouTrack**: no repo arg; only issue IDs and `--query`.
- Global `smith code search` / `smith prs search` target all enabled remotes and reject `--project`/`--repo`.

### Pipeline specifics

- `pipelines grep ... <id>` expects a pipeline/run/build ID. Use `pipelines list` first to find the parent ID, then grep with `--log-id <job-id>`.
- `pipelines list` prints a compact DAG (`@` pipelines, `#` stages, `*` jobs). Filter with `--status`, `--grep`, `--skip`/`--take`, `--max-depth` (GitLab only). Grammar in `references/pipelines-format.md`.
- GitLab `prs get` includes `pipeline_id` and `pipeline_status`.

## Investigation Algorithm

You have access to tools to deeply investigate repositories code and documentation. Never yield until you've explored **multiple perspectives** and are confident in your answer.

Evidence hierarchy:

- **Primary proof**: non-example implementation code, configuration, migrations, scripts, or executable tests that exercise real behavior.
- **Supporting proof**: PR/MR discussion, pipeline/build logs, or story context.
- **Weak context only**: Readme or examples code.

1. **Go broad**: for code: `smith code search "<stable noun>" --glob "<glob>"`; for stories: `smith <youtrack-remote> stories search --query "<query>"`.
2. **Map the subtree**: `smith <remote> code grep <scope> ".*" --output-mode files_with_matches --path <dir>`.
3. **Reject weak sources early**: when hits are in examples, docs or readmes, treat them as leads only. Continue searching for primary proof before answering.
4. **Extract proof from multiple perspectives**: `smith <remote> code grep <scope> "<regex>"` — narrow in order: repo → `--path` → `--glob` → regex → `--from-line`/`--to-line`. Use `--no-clone` for one-off scans across many repos.
5. **Seek the truth**: Keep repeating 1-4 until you find **actual implementation or executable test usage**. Try different angles and perspectives.
6. **Corroborate when needed**: `prs` for review context, `pipelines` for build evidence (use `--reverse` for error logs so latest hits survive truncation), `stories` for work-item context. Download story image attachments to `/tmp` and read before concluding.
7. **Report** only what primary or supporting evidence proves. Cite URLs.

### Pipeline analysis flow

1. `pipelines list <scope> <pipeline_id> --status failed` to focus on failures.
2. `pipelines grep <scope> <pipeline_id> "<regex>" --log-id <job-id> --reverse` for log analysis.
3. GitLab: if logs indicate redirected output, use `pipelines artifacts list`/`grep` to search within artifacts.

## Stop Conditions

Stop and answer when:

- Required evidence is in hand.
- All narrowing paths tried and empty — respond with `not enough evidence` and one next command.
- A recovery loop retried once without progress — surface the blocker.

## Failure Handling

Full matrix in `references/failure-playbook.md`, credentials in `references/auth-troubleshooting.md`.

- **401/403** — confirm remote in config, token env var set, run provider login, retry once.
- **429** — lower `--take`, narrow `--path`/`--glob`/regex; for GitHub grep, reduce `GITHUB_GREP_MAX_WORKERS`.
- **Truncation** — narrow `--path`/`--glob`, page with `--from-line`/`--to-line`, reduce `--context-lines`.
- **Empty results** — broaden query, drop strict filters, rerun grep on candidate repo.
- **Wrong repository** — rerun broader `code search`; check repo argument format per rules above.

## Answer Contract

- Findings first, evidence-based. Cite `<scope>/<repo>:<path>` (or `repo:path` if caller requests).
- Single-remote: flat. Multi-remote: split by remote.
- Unresolved: `not enough evidence` + one next command.
- End with `Sources` section of absolute URLs.

### URL templates

Build absolute URLs from remote's `host` and `org` (`smith config show <remote>`). Defaults: `github.com`, `gitlab.com`, `dev.azure.com`. Use `HEAD` for default branch; swap in branch/SHA for permalinks.

| Type | GitHub | GitLab | Azure DevOps |
|---|---|---|---|
| File | `https://<host>/<org>/<repo>/blob/HEAD/<path>#L<line>` | `https://<host>/<grp>/<proj>/-/blob/HEAD/<path>#L<line>` | `https://dev.azure.com/<org>/<proj>/_git/<repo>?path=/<path>&line=<line>&_a=contents` |
| PR/MR | `https://<host>/<org>/<repo>/pull/<id>` | `https://<host>/<grp>/<proj>/-/merge_requests/<id>` | `https://dev.azure.com/<org>/<proj>/_git/<repo>/pullrequest/<id>` |
| Pipeline | `https://<host>/<org>/<repo>/actions/runs/<id>` | `https://<host>/<grp>/<proj>/-/pipelines/<id>` | `https://dev.azure.com/<org>/<proj>/_build/results?buildId=<id>` |
| Issue | `https://<host>/<org>/<repo>/issues/<id>` | `https://<host>/<grp>/<proj>/-/issues/<id>` | `https://dev.azure.com/<org>/<proj>/_workitems/edit/<id>` |

YouTrack: `https://<host>/issue/<id>`. Azure DevOps legacy hosts: use remote's `host` in place of `dev.azure.com/<org>`.