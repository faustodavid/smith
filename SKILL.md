---
name: smith
description: Read-only Azure DevOps and GitHub investigation skill for code search, grep, pull request review context, build-log analysis, and work-item discovery using local single-user credentials. Use when prompts ask to find where configuration lives, trace infrastructure keys, inspect PR or CI failures, or map unknown paths in Azure DevOps or GitHub repositories. Do not use for write actions (create, update, approve, post), generic internet research, or non-DevOps creative tasks.
---

# Smith

Run Azure DevOps and GitHub investigations with a deterministic broad-to-narrow workflow and evidence-first outputs.

## Trigger Decision

### Use smith when

- The user asks where configuration is defined in Azure DevOps repositories.
- The user asks to locate Terraform, Helm, YAML, or code keys in Azure DevOps repos.
- The user asks to locate Terraform, Helm, YAML, or code keys in GitHub repos for a known org.
- The user asks to inspect pull requests, build logs, or work-item context.
- The user asks to map an unknown path, module, or service ownership in Azure DevOps code.
- The user asks to investigate incidents rooted in repository config or CI logs.

### Do not use smith when

- The task requires write operations (create or update work items, PR comments, approvals, Slack posting).
- The task is generic internet research.
- The task is non-DevOps creative writing or unrelated productivity work.

### Ambiguous request fallback

- Start with `code search` to discover candidate repositories and paths.
- If query intent is still unclear, return findings plus the best next narrowing command.

## Rules

1. Work autonomously and complete the investigation loop.
- Do the research with tools.
- Do not ask the user to inspect files or logs manually when CLI commands can retrieve them.

2. Never guess.
- If uncertain, run another narrowing command.
- If information remains unresolved after search and grep, say so explicitly.

3. Use broad-to-narrow exploration.
- Start broad with `code search`.
- Map structure with `code grep --output-mode files_with_matches`.
- Extract proof with focused `code grep --output-mode content`.

4. Prefer Git evidence over assumptions.
- Treat repository config and Terraform/GitOps definitions as source of truth.
- Corroborate with PR/build/board data only when needed.

5. Cite concrete source paths.
- Include `project/repository:path` evidence in conclusions.
- For requested changes, identify exact files and keys.

6. Keep output practical.
- Default to `--format text` for investigation loops.
- Use `--format json` for deterministic parsing only.

## Investigation Algorithm

1. Preflight.
- Ensure `AZURE_DEVOPS_ORG_URL` is set.
- Ensure `GITHUB_ORG` is set when provider includes GitHub.
- Ensure `az login` is valid for the target org.
- Ensure `gh auth status` is valid when provider includes GitHub and `GITHUB_TOKEN` is not set.
- Capture initial scope (project or repo if provided).

2. Discovery.
- Run `code search "..."` to find candidate repos and paths.

3. Structure map.
- Run `code grep --output-mode files_with_matches` to map repository layout for the relevant path.

4. Focused extraction.
- Run `code grep --output-mode content` with `--path`, `--glob`, and regex refinement.
- Use `--from-line` and `--to-line` for targeted pagination.

5. Corroboration (optional).
- Use `pr list`, `pr get`, and `pr threads` when changes, ownership context, or review discussion context matters.
- Use `build logs` and `build grep` when CI failure context matters.
- Use `board search`, `board ticket`, or `board mine` when work-item linkage matters.

6. Final answer.
- Provide concise conclusion plus evidence paths.
- If unresolved, state "not enough evidence" and provide the next narrowing command.

## Stop Conditions

### Enough evidence

Stop and answer when all are true:
- At least one direct source path supports the conclusion.
- The conclusion is specific enough to act on.
- Any uncertainty is explicitly called out.

### No evidence

Stop and report unresolved when all are true:
- Broad search returns no relevant candidates.
- At least one narrowing attempt was executed.
- The output includes a concrete next command the user can run.

## Failure Handling Flow

1. 401 or 403 authentication rejected.
- Re-authenticate with `az login`.
- For GitHub, set `GITHUB_TOKEN` or run `gh auth login`.
- Retry the same command once.

2. 429 rate limited.
- Reduce breadth (`--take`, path scope, regex breadth).
- Retry with narrower scope.

3. Truncation.
- Narrow with `--path` and `--glob`.
- Page with `--from-line` and `--to-line`.

4. Empty results.
- Broaden search terms in `code search`.
- Remove restrictive filters, then narrow again.

5. Wrong repository scope.
- Return to `code search` and remap candidate repositories before deeper grep.

6. Provider incompatibility.
- If a command is unsupported for a provider, return a concrete fallback command.
- Example: for GitHub `board list`, use `board search`.

## Commands (Read-Only)

- `code search` is the only fanout command:
  - `code search <text> [--project <name>] [--repos r1,r2] [--skip N] [--take N] [--provider azdo|github|all] [--format text|json]`
- Discovery:
  - `projects list azdo [--format text|json]`
  - `projects list github [--format text|json]`
  - `repos list azdo <project> [--format text|json]`
  - `repos list github [--format text|json]`
- Code:
  - `code grep azdo <project> <repo> [<regex>] [--path <path>] [--branch <branch>] [--glob <glob>] [--output-mode content|files_with_matches|count] [--context-lines N] [--from-line N] [--to-line N] [--case-sensitive] [--format text|json]`
  - `code grep github <repo> [<regex>] [--path <path>] [--branch <branch>] [--glob <glob>] [--output-mode content|files_with_matches|count] [--context-lines N] [--from-line N] [--to-line N] [--case-sensitive] [--format text|json]`
- Pull requests:
  - `pr list azdo <project> <repo> [--status active,completed,abandoned] [--creator user1,user2] [--date-from ISO] [--date-to ISO] [--skip N] [--take N] [--exclude-drafts] [--include-labels] [--format text|json]`
  - `pr list github <repo> [--status active,completed,abandoned] [--creator user1,user2] [--date-from ISO] [--date-to ISO] [--skip N] [--take N] [--exclude-drafts] [--include-labels] [--format text|json]`
  - `pr get azdo <project> <repo> <id> [--format text|json]`
  - `pr get github <repo> <id> [--format text|json]`
  - `pr threads azdo <project> <repo> <id> [--format text|json]`
  - `pr threads github <repo> <id> [--format text|json]`
- Build logs:
  - `build logs azdo <project> <id> [--format text|json]`
  - `build logs github <repo> <id> [--format text|json]`
  - `build grep azdo <project> <id> [--log-id N] [--pattern <regex>] [--output-mode content|logs_with_matches|count] [--context-lines N] [--from-line N] [--to-line N] [--case-sensitive] [--format text|json]`
  - `build grep github <repo> <id> [--log-id N] [--pattern <regex>] [--output-mode content|logs_with_matches|count] [--context-lines N] [--from-line N] [--to-line N] [--case-sensitive] [--format text|json]`
- Board read (`stories` is an alias of `board`):
  - `board ticket azdo <project> <id> [--format text|json]`
  - `board ticket github <repo> <id> [--format text|json]`
  - `board list azdo <project> --wiql "<query>" [--skip N] [--take N] [--format text|json]`
  - `board search azdo <project> --query <text> [--area <path>] [--type <work_item_type>] [--state <state>] [--assigned-to <email>] [--skip N] [--take N] [--format text|json]`
  - `board search github <repo> --query <text> [--type <work_item_type>] [--state <state>] [--assigned-to <email>] [--skip N] [--take N] [--format text|json]`
  - `board mine azdo <project> [--include-closed] [--skip N] [--take N] [--format text|json]`
  - `board mine github <repo> [--include-closed] [--skip N] [--take N] [--format text|json]`
  - `stories ticket azdo <project> <id> [--format text|json]`

## Auth and Config

- Required env var:
  - `AZURE_DEVOPS_ORG_URL` (example `https://dev.azure.com/rtl-nl`)
- Required for GitHub provider:
  - `GITHUB_ORG` (example `rtl-nl`)
- Optional env vars:
  - `AZURE_DEVOPS_API_VERSION` (default `7.1`)
  - `AZURE_DEVOPS_TIMEOUT_SECONDS` (default `30`)
  - `THANOS_LOCAL_MAX_OUTPUT_CHARS` (default `10240`)
  - `GITHUB_TOKEN` (preferred GitHub auth token)
  - `GITHUB_API_URL` (default `https://api.github.com`)
  - `GITHUB_API_VERSION` (default `2022-11-28`)
  - `GITHUB_TIMEOUT_SECONDS` (default follows runtime timeout)

Auth model is single-user only:
- Acquire token with `DefaultAzureCredential(exclude_interactive_browser_credential=True)`.
- Scope `499b84ac-1321-427f-aa17-267ca6975798/.default`.
- Retry once on 401/403 with a fresh token.
- If auth still fails, run `az login` and retry.
- For GitHub, use `GITHUB_TOKEN` first and fallback to `gh auth token`.

## Integrations

- Codex skill install or update:
  - `bash scripts/install_codex_skill.sh`
- Claude commands install or update:
  - `bash scripts/install_claude_commands.sh`
  - `bash scripts/install_claude_commands.sh /path/to/repo`
- After template updates, re-sync Claude commands in target repos using the install script.

## References

- `references/usage-recipes.md`
- `references/auth-troubleshooting.md`
- `references/trigger-cases.md`
- `references/behavioral-quality-gates.md`
- `references/failure-playbook.md`
