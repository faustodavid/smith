---
name: smith
description: Read-only Azure DevOps and GitHub investigation skill for locating code/config, grepping repos, inspecting pull requests or pipeline failures, and reading work items/issues. Use it whenever the user needs repo-backed evidence from Azure DevOps or GitHub, including vague requests like "where is X set?" or "investigate this failure".
---

# Smith

Use `smith` for deterministic Azure DevOps and GitHub investigations. Keep the main skill lean and open bundled references only when needed.

## Trigger

- Use `smith` for read-only code/config discovery, PR review context, pipeline-log analysis, and work-item or issue lookup in Azure DevOps or GitHub.
- Also use it for ambiguous investigation requests when the likely source of truth is an Azure DevOps or GitHub repo.
- Do not use it for write operations or public-web research.

## Workflow

1. Stay read-only and complete the investigation with CLI commands instead of asking the user to inspect files or logs manually.
2. Never guess. If repo, path, or cause is unclear, run another narrowing command.
3. Start broad when scope is unknown:
   - `smith code search "<query>" [--provider azdo|github|all]`
4. Map structure before deep extraction:
   - `smith code grep azdo <project> <repo> ".*" --output-mode files_with_matches`
   - `smith code grep github <repo> ".*" --output-mode files_with_matches`
5. Extract proof with focused grep:
   - `smith code grep azdo <project> <repo> "<regex>" --output-mode content [--path <path>] [--glob <glob>]`
   - `smith code grep github <repo> "<regex>" --output-mode content [--path <path>] [--glob <glob>]`
6. Use `prs`, `pipelines logs`, and `stories` when the request is explicitly about those artifacts or they are needed to corroborate repo evidence.
7. If still unresolved, say `not enough evidence` and give one concrete next command.

## Output

- Prefer repository files as source of truth; use PR, pipeline, or story data as supporting evidence.
- Cite exact sources as `project/repository:path` or `org/repository:path`.
- Keep single-provider answers flat. Separate provider sections only when results come from multiple providers.
- When the user asks where something is set, identify the exact file and key if you can.

## Load On Demand

- `references/trigger-cases.md` for trigger edge cases.
- `references/usage-recipes.md` for exact command patterns.
- `references/failure-playbook.md` for auth, empty-result, truncation, rate-limit, or unsupported-command recovery.
- `references/auth-troubleshooting.md` only when env or auth is blocking progress.
- `references/behavioral-quality-gates.md` for a final quality check on non-trivial investigations.
