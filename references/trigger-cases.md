# Trigger Cases

Use `smith` when the request is read-only and the likely source of truth is Azure DevOps, GitHub, or GitLab.

## Positive

- Find where code or config is defined.
- Search a repo for a key, resource, regex, or file.
- Inspect a pull request, merge request, changed files, review threads, or related code paths.
- Inspect pipeline logs or a build failure.
- Read work items, GitHub issues, or GitLab issues.

## Negative

- Create, update, approve, or comment on anything.
- Post to Slack or another external system.
- Search public web docs or general internet sources.
- Do unrelated writing or creative analysis.

## Ambiguous

- "Where is X configured?"
- "Find the TTL setting."
- "Investigate this failure."

Default: start with `smith code search "<query>"`, then narrow.

## Quick Check

1. Is GitHub, GitLab, or Azure DevOps the target?
2. Is the request read-only?
3. Is repo, PR, pipeline, or story evidence needed?
4. If unsure, start broad.
