---
name: smith
description: Use when the task is a read-only GitHub or Azure DevOps investigation that needs repo, PR, pipeline, or story evidence. Do not use for write operations, public-web research, or unrelated analysis.
---

# Smith

Run Azure DevOps and GitHub investigations with a deterministic broad-to-narrow workflow and evidence-first outputs.

## Trigger Decision

### Use smith when

- The user asks to search, locate, or grep code or config in GitHub or Azure DevOps.
- The user asks to inspect pull requests, review threads, build failures, or pipeline logs.
- The user asks to read work items or GitHub issues.
- The user asks "Where is X configured?" and the likely source of truth is a repository, PR, pipeline, or story.

### Do not use smith when

- The task requires a write operation.
- The task is public-web or general internet research.
- The task is unrelated writing, brainstorming, or creative work.
- Another tool is clearly the source of truth.

### Ambiguous request fallback

- If GitHub or Azure DevOps is the likely source of truth, start broad with `smith code search "<query>"`.
- If repo or project scope is unknown, use discovery helpers first.
- If the request is still unclear after discovery, return the findings so far and the best next narrowing command.

## Command Vocabulary

Use the current CLI tree only:

- Discovery:
  - `smith orgs azdo`
  - `smith orgs github`
  - `smith repos azdo [<project>]`
  - `smith repos github`
- Code:
  - `smith code search "<query>"`
  - `smith code grep azdo <project> <repo> "<regex>"`
  - `smith code grep github <repo> "<regex>"`
- Pull requests:
  - `smith prs list|get|threads azdo ...`
  - `smith prs list|get|threads github ...`
- Pipelines:
  - `smith pipelines logs list|grep azdo ...`
  - `smith pipelines logs list|grep github ...`
- Stories and issues:
  - `smith stories get|search|mine azdo ...`
  - `smith stories get|search|mine github ...`

Representative command examples:

- `smith prs list github <repo>`
- `smith pipelines logs list github <repo> <id>`
- `smith stories search azdo <project> --query "<text>"`

Do not invent legacy paths such as `discover`, `organizations`, `work`, `ci`, `board`, or `stories ticket`.

## Provider Argument Rules

`code grep`, `prs`, `pipelines logs`, and `stories` use provider-positional arguments.

- Azure DevOps commands take `<project>` and, when needed, `<repo>`.
- GitHub commands take a bare `<repo>` slug without the org prefix, not `org/repo`.

Important GitHub repo-shape rule:

- Search results and inline evidence often look like `org/repo:path`.
- GitHub command arguments are still bare `<repo>`.

Correct:

- `smith code grep github openai-python "OPENAI_WEBHOOK_SECRET"`
- `smith prs get github openai-python 42`
- `smith pipelines logs list github openai-python 12345`
- `smith stories get github openai-python 77`

Wrong:

- `smith code grep github openai/openai-python "OPENAI_WEBHOOK_SECRET"`
- `smith prs get github openai/openai-python 42`

## Investigation Algorithm

1. Confirm the request is read-only and GitHub or Azure DevOps backed.
2. Discover candidate scope.
   - If repo, project, or file is unknown, start with `smith code search "<query>"`.
   - If org, project, or repo scope is unclear, use `smith orgs ...` or `smith repos ...`.
3. Map repository structure only when needed.
   - Azure DevOps: `smith code grep azdo <project> <repo> ".*" --output-mode files_with_matches`
   - GitHub: `smith code grep github <repo> ".*" --output-mode files_with_matches`
4. Extract proof from the smallest possible scope.
   - Azure DevOps: `smith code grep azdo <project> <repo> "<regex>" --output-mode content [--path <path>] [--glob <glob>]`
   - GitHub: `smith code grep github <repo> "<regex>" --output-mode content [--path <path>] [--glob <glob>]`
5. Corroborate only when needed.
   - Use `prs` for review context or changed-file confirmation.
   - Use `pipelines logs` for build failures or runtime evidence.
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
- Truncation:
  - Narrow `--path` and `--glob`, or page with `--from-line` and `--to-line`.
- Empty results:
  - Broaden the search query or remove strict filters.
- Wrong repository:
  - Remap the repo with `smith code search "<broader query>"`.
  - For GitHub, if a command 404s after using `org/repo`, rerun it with the bare `<repo>` slug.

Use `references/auth-troubleshooting.md` for env or credential setup and `references/failure-playbook.md` for recovery details.

## Answer Contract

- Findings come first. Keep them concise and evidence-based.
- Cite exact paths in the body when you reference code or logs:
  - Azure DevOps: `project/repository:path`
  - GitHub: `org/repository:path`
- End with a `Sources` section.
- In `Sources`, use exact provider paths unless the caller explicitly requires `repo:path`.
- Multi-provider answers should be split by provider.
- Unresolved answers must include `not enough evidence` and one next command.

## Preflight And Environment

- Set org context before running provider commands:
  - `export AZURE_DEVOPS_ORG="<org>"`
  - `export GITHUB_ORG="<org>"`
- Authenticate when needed:
  - Azure DevOps: `az login`
  - GitHub: `export GITHUB_TOKEN="<token>"` or `gh auth login`
- Per-invocation overrides are available:
  - `smith --azdo-org <org> ...`
  - `smith --github-org <org> ...`

Some benchmark harnesses expose only a GitHub subset such as `code search`, `code grep github`, `orgs github`, and `repos github`. In that environment, stay within the exposed subset instead of switching tools.
