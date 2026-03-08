# Behavioral Quality Gates

A hardened run is acceptable only if all gates pass.

## Gate 1: Trigger quality

- Positive requests route to `smith`.
- Negative requests are rejected as out of scope.
- Ambiguous requests start with discovery-first behavior.

## Gate 2: Investigation discipline

- Broad-to-narrow sequence is followed:
  1. `code search`
  2. `code grep --output-mode files_with_matches` when structure is needed
  3. focused `code grep --output-mode content`
- PR/pipeline/work tools are used only when needed to corroborate.
- Provider selection is explicit via positional syntax for deep commands.
  - `code grep azdo <project> <repo>`
  - `code grep github <repo>`
  - `pipelines logs list github <repo> <id>`

## Gate 3: Evidence output quality

- Final answer includes source paths in `project/repository:path` format.
- Results identify source provider (azdo/github) and keep provider sections separate.
- Single-provider text output is flat (no provider section header); grouped sections are preserved for multi-provider search.
- If unresolved, answer includes the phrase "not enough evidence" and one explicit next command.

## Gate 4: Failure-path behavior

- 401/403 path includes re-auth with `az login`.
- GitHub auth path includes `GITHUB_TOKEN` or `gh auth login`.
- 429 path includes narrowing scope and retry.
- Truncation path includes path/glob/range narrowing.
- Empty-result path includes broadening query then narrowing again.

## Gate 5: Documentation completeness

- `SKILL.md` includes trigger decisions, anti-triggers, workflow, stop conditions, and failure handling.
- Recipes, failure playbook, and trigger cases are linked and consistent.
- Claude templates include preflight, required args, first command, fallback, and evidence output contract.
