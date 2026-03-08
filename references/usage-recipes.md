# Usage Recipes

For non-trivial requests, follow this order first:

1. Read `references/trigger-cases.md`.
2. Read `references/behavioral-quality-gates.md`.
3. Read `references/failure-playbook.md`.
4. Then execute commands from this recipe file.

## Prerequisites

```bash
export AZURE_DEVOPS_ORG="<org>"
export GITHUB_ORG="<org>"
az login
```

## GitHub grep performance tuning

For large repos, keep grep exact but reduce wall time with bounded parallel fetch:

```bash
export GITHUB_GREP_ENABLE_PARALLEL=true
export GITHUB_GREP_MAX_WORKERS=8
```

Narrow first to reduce API volume:

```bash
smith code grep github <repo> "<regex>" --path <path> --glob "<glob>" --branch <branch>
```

If your network or org rate-limits aggressively, lower workers or force sequential:

```bash
export GITHUB_GREP_MAX_WORKERS=1
# or
export GITHUB_GREP_ENABLE_PARALLEL=false
```

## Investigation pattern (QueryAgent-inspired)

1. Broad discovery:
```bash
smith code search "<topic keywords>" --take 30
```

2. Structure mapping:
```bash
smith code grep azdo <project> <repo> ".*" --path / --output-mode files_with_matches
```

3. Focused extraction:
```bash
smith code grep azdo <project> <repo> "<regex>" --path <path> --glob "<glob>" --context-lines 2
smith code grep github <repo> "<regex>" --path <path> --glob "<glob>" --context-lines 2
```

4. Report with evidence:
- Include exact `project/repository:path` sources.
- If unresolved, state "not enough evidence" and provide the next narrowing command.

## List orgs and repos

```bash
smith orgs azdo
smith orgs github
smith repos azdo SRE
smith repos azdo
smith repos github
```

## Broad search, then targeted grep

```bash
smith code search "grafana AND path:*alerts*"
smith code search "grafana" --provider github
smith code grep azdo SRE rtl-devops-gitops "severity" --path /alerts --glob "*.yaml" --context-lines 2
smith code grep github rtl-devops-gitops "severity" --glob "*.yaml" --context-lines 2
```

## Pull request investigation

```bash
smith prs list azdo SRE rtl-devops-gitops --status active,completed --take 25
smith prs get azdo SRE rtl-devops-gitops 12345
smith prs threads azdo SRE rtl-devops-gitops 12345
smith prs list github rtl-devops-gitops --status active,completed
smith prs get github rtl-devops-gitops 12345
smith prs threads github rtl-devops-gitops 12345
```

## Pipeline log investigation

```bash
smith pipelines logs list azdo SRE 942510
smith pipelines logs grep azdo SRE 942510 --pattern "ERROR|Exception" --output-mode logs_with_matches
smith pipelines logs grep azdo SRE 942510 --log-id 18 --from-line 380
smith pipelines logs list github rtl-devops-gitops <run_id>
smith pipelines logs grep github rtl-devops-gitops <run_id> --pattern "ERROR|Exception"
```

## Work item and issue read workflows

```bash
smith stories get azdo SRE 123456
smith stories search azdo SRE --query "login error" --state Active
smith stories search azdo SRE --query "Bug login error" --type Bug
smith stories mine azdo SRE
smith stories get github rtl-devops-gitops 123
smith stories search github rtl-devops-gitops --query "retention"
smith stories mine github rtl-devops-gitops
```

## JSON output for automation

```bash
smith code search "terraform" --format json
smith code grep github rtl-devops-gitops "grafana.*" --format json
```

## Re-sync Claude commands after template updates

```bash
bash skills/smith/scripts/install_claude_commands.sh . copy
```
