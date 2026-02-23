# Trigger Cases

Use this matrix to decide whether `smith` should be used.

## Positive triggers (should use smith)

- Find where Loki retention is configured in Azure DevOps repos.
- Inspect failing build logs for a specific build ID.
- Find which repository contains a Terraform resource or config key.
- Inspect PR details and changed files for a known project and repo.
- Find work items related to an incident keyword in Azure DevOps.

## Negative triggers (should not use smith)

- Create or update a work item.
- Post messages to Slack.
- Review public internet documentation.
- Investigate GitHub-only repositories without Azure DevOps context.
- Non-DevOps creative writing tasks.

## Ambiguous triggers (use discovery-first)

- "Where is X configured?"
- "Find the TTL setting"
- "Investigate this failure"

When ambiguous, start with `code search` before narrowing to grep or PR/build/board commands.

## Trigger checklist

1. Is the target system Azure DevOps?
2. Is the request read-only?
3. Does the request involve code/config/PR/build/work-item investigation?
4. If any answer is unknown, start broad with discovery and then narrow.
