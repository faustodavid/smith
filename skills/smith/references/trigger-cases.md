# Trigger Cases

Sanity check a prompt against these examples before invoking smith.

## Positive

- Locate where a key, resource, regex, or config file lives.
- Inspect a pull/merge request, review threads, or changed files.
- Read pipeline logs or investigate a build failure.
- Read or search work items and issues (GitHub, GitLab, YouTrack).

## Negative

- Create, update, approve, or comment on anything.
- Post to Slack or another external system.
- Search public web docs or general internet sources.
- Unrelated writing or creative analysis.

## Ambiguous

- "Where is X configured?"
- "Find the TTL setting."
- "Investigate this failure."

Default: `smith code search "<stable noun>"`, then narrow.

## Quick Check

1. Is GitHub, GitLab, Azure DevOps, or YouTrack the target?
2. Is the request read-only?
3. Is repo, PR, pipeline, or story evidence needed?
4. If unsure, start broad.
