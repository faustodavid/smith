# Behavioral Quality Gates

Self-review checklist. An investigation is acceptable only if every gate passes.

## Gate 1 — Trigger

- [ ] Request is read-only.
- [ ] Source of truth is GitHub, GitLab, Azure DevOps, or YouTrack.
- [ ] Ambiguous prompts start with discovery or `smith code search`.

## Gate 2 — Investigation

- [ ] Unknown scope starts broad (`smith code search` or `smith <remote> code search`).
- [ ] Unknown org/project scope uses `smith <remote> orgs`, `smith <remote> repos`, or GitLab `smith <gitlab-remote-name> groups`.
- [ ] Unknown file structure uses `smith <remote> code grep ... ".*" --output-mode files_with_matches` before wider regex.
- [ ] Final evidence comes from focused `smith <remote> code grep`.
- [ ] `prs`, `pipelines`, `stories` are used only for corroboration.
- [ ] GitHub follow-ups use bare `<repo>`. GitLab follow-ups use full `group/project`.

## Gate 3 — Answer Quality

- [ ] Every claim cites `project/repository:path`, `org/repository:path`, or `group/repository:path`.
- [ ] Single-remote answers are flat; multi-remote answers split by remote.
- [ ] Unresolved answers include `not enough evidence` and one next command.
- [ ] Final line is a `Sources` section of absolute URLs.

## Gate 4 — Recovery

- [ ] Auth blockers → `references/auth-troubleshooting.md`.
- [ ] 401 or 403 / 429 / Truncation / Empty results / Wrong repository → `references/failure-playbook.md`.
- [ ] Do not stop while another narrowing command could resolve the request.
