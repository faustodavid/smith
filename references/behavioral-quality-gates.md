# Behavioral Quality Gates

A `smith` investigation is acceptable only if all gates pass.

## Gate 1: Trigger

- The request is read-only and Azure DevOps or GitHub backed.
- Ambiguous requests start with discovery.
- Out-of-scope requests are not handled with `smith`.

## Gate 2: Investigation

- Unknown scope starts with `code search`.
- Unknown structure uses `code grep ... ".*" --output-mode files_with_matches`.
- Final evidence comes from focused `code grep`.
- `prs`, `pipelines logs`, and `stories` are used only when primary or corroborating evidence is needed.
- Provider syntax is explicit:
  - `code grep azdo <project> <repo> "<regex>"`
  - `code grep github <repo> "<regex>"`
  - `pipelines logs list github <repo> <id>`

## Gate 3: Answer Quality

- The answer cites exact `project/repository:path` or `org/repository:path` sources.
- Single-provider answers stay flat; multi-provider answers split by provider.
- Unresolved answers include `not enough evidence` plus one next command.

## Gate 4: Recovery

- Auth or env blockers use `references/auth-troubleshooting.md`.
- Empty, rate-limited, truncated, or wrong-repo cases use `references/failure-playbook.md`.
- Do not stop while another narrowing command could resolve the request.
