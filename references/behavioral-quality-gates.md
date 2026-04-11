# Behavioral Quality Gates

A `smith` investigation is acceptable only if all gates pass.

## Gate 1: Trigger

- The request is read-only and GitHub, GitLab, or Azure DevOps backed.
- Ambiguous requests start with discovery.
- Out-of-scope requests are not handled with `smith`.

## Gate 2: Investigation

- Unknown scope starts with `smith code search "<query>"`.
- Unknown org or repo scope can use `smith orgs ...` and `smith repos ...`.
- Unknown structure uses `smith code grep ... ".*" --output-mode files_with_matches`.
- Final evidence comes from focused `smith code grep`.
- `prs`, `pipelines logs`, and `stories` are used only when primary or corroborating evidence is needed.
- Remote syntax is explicit:
  - `smith code grep <azdo-remote-name> <project> <repo> "<regex>"`
  - `smith code grep <github-remote-name> <repo> "<regex>"`
  - `smith code grep <gitlab-remote-name> <repo> "<regex>"`
  - `smith pipelines logs list <github-remote-name> <repo> <id>`
- GitHub repo arguments stay bare:
  - Search results may look like `org/repo:path`
  - Follow-up GitHub commands still take `<repo>`, not `org/repo`
- GitLab repo arguments stay group-relative:
  - Search results may look like `group/repo:path`
  - Follow-up GitLab commands take `<repo>` relative to the configured remote's group, not full `group/repo`

## Gate 3: Answer Quality

- The answer cites exact `project/repository:path`, `org/repository:path`, or `group/repository:path` evidence.
- The answer ends with a `Sources` section.
- Single-remote answers stay flat; multi-remote answers split by remote.
- Unresolved answers include `not enough evidence` plus one next command.
- If a caller explicitly requires `repo:path`, the `Sources` section follows that contract.

## Gate 4: Recovery

- Auth or env blockers use `references/auth-troubleshooting.md`.
- 401 or 403, 429, Truncation, Empty results, or Wrong repository cases use `references/failure-playbook.md`.
- Do not stop while another narrowing command could resolve the request.
