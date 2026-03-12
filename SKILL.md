---
name: smith
description: Read-only GitHub and Azure DevOps crossed provider and repo investigation skill for code search, grep, pull request review context, build-log analysis, and work-item discovery using local single-user credentials.
---

# Smith

Run Azure DevOps and GitHub investigations with a deterministic broad-to-narrow workflow and evidence-first outputs.

## Trigger Decision

### Use smith when

- The user asks to search, locate, or grep code/config across Azure DevOps and/or GitHub repos.
- The user asks to review pull requests, pipeline logs, or build failures.
- The user asks to find, list, or inspect work items / stories.

### Do not use smith when

- for write operations.

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
- Start broad with an explicit query:
  - `smith code search "<query>"`
- Map structure with provider-positional grep (required args are mandatory):
  - Azure DevOps: `smith code grep azdo <project> <repo> ".*" --output-mode files_with_matches`
  - GitHub: `smith code grep github <repo> ".*" --output-mode files_with_matches`
- Extract proof with focused provider-positional grep:
  - Azure DevOps: `smith code grep azdo <project> <repo> "<regex>" --output-mode content [--path <path>] [--glob <glob>]`
  - GitHub: `smith code grep github <repo> "<regex>" --output-mode content [--path <path>] [--glob <glob>]`
- Important:
  - For `code grep`, provider is positional (`azdo|github`).
  - For Azure DevOps grep, both `<project>` and `<repo>` are required.

4. Prefer Git evidence over assumptions.
- Treat repository files as source of truth.
- Corroborate with pull-request/pipeline/work data only when needed.

5. Cite concrete source paths.
- Always include `(org|project)/repository:path` evidence as source at the end of the answer.
- For requested changes, identify exact files and keys.
