# Usage Recipes

For non-trivial requests, follow this order first:

1. Read `references/trigger-cases.md`.
2. Read `references/behavioral-quality-gates.md`.
3. Read `references/failure-playbook.md`.
4. Then execute commands from this recipe file.

## Prerequisites

```bash
export AZURE_DEVOPS_ORG_URL="https://dev.azure.com/<org>"
export GITHUB_ORG="<org>"
az login
```

## Investigation pattern (QueryAgent-inspired)

1. Broad discovery:
```bash
python3 skills/smith/scripts/smith_cli.py code search --query "<topic keywords>" --take 30
```

2. Structure mapping:
```bash
python3 skills/smith/scripts/smith_cli.py code grep azdo <project> <repo> --path / --output-mode files_with_matches --pattern ".*"
```

3. Focused extraction:
```bash
python3 skills/smith/scripts/smith_cli.py code grep azdo <project> <repo> --path <path> --glob "<glob>" --pattern "<regex>" --context-lines 2
python3 skills/smith/scripts/smith_cli.py code grep github <repo> --path <path> --glob "<glob>" --pattern "<regex>" --context-lines 2
```

4. Report with evidence:
- Include exact `project/repository:path` sources.
- If unresolved, state "not enough evidence" and provide the next narrowing command.

## Discover projects and repos

```bash
python3 skills/smith/scripts/smith_cli.py projects list azdo
python3 skills/smith/scripts/smith_cli.py projects list github
python3 skills/smith/scripts/smith_cli.py repos list azdo SRE
python3 skills/smith/scripts/smith_cli.py repos list github
```

## Broad search, then targeted grep

```bash
python3 skills/smith/scripts/smith_cli.py code search --query "grafana AND path:*alerts*"
python3 skills/smith/scripts/smith_cli.py code search --query "grafana" --provider github
python3 skills/smith/scripts/smith_cli.py code grep azdo SRE rtl-devops-gitops --path /alerts --glob "*.yaml" --pattern "severity" --context-lines 2
python3 skills/smith/scripts/smith_cli.py code grep github rtl-devops-gitops --glob "*.yaml" --pattern "severity" --context-lines 2
```

## Pull request investigation

```bash
python3 skills/smith/scripts/smith_cli.py pr list azdo SRE rtl-devops-gitops --status active,completed --take 25
python3 skills/smith/scripts/smith_cli.py pr get azdo SRE rtl-devops-gitops 12345
python3 skills/smith/scripts/smith_cli.py pr threads azdo SRE rtl-devops-gitops 12345
python3 skills/smith/scripts/smith_cli.py pr list github rtl-devops-gitops --status active,completed
python3 skills/smith/scripts/smith_cli.py pr get github rtl-devops-gitops 12345
python3 skills/smith/scripts/smith_cli.py pr threads github rtl-devops-gitops 12345
```

## Build log investigation

```bash
python3 skills/smith/scripts/smith_cli.py build logs azdo SRE 942510
python3 skills/smith/scripts/smith_cli.py build grep azdo SRE 942510 --pattern "ERROR|Exception" --output-mode logs_with_matches
python3 skills/smith/scripts/smith_cli.py build grep azdo SRE 942510 --log-id 18 --from-line 380
python3 skills/smith/scripts/smith_cli.py build logs github rtl-devops-gitops <run_id>
python3 skills/smith/scripts/smith_cli.py build grep github rtl-devops-gitops <run_id> --pattern "ERROR|Exception"
```

## Board and stories read workflows

```bash
python3 skills/smith/scripts/smith_cli.py board ticket azdo SRE 123456
python3 skills/smith/scripts/smith_cli.py board list azdo SRE --wiql "Select [System.Id], [System.Title] From WorkItems Where [System.WorkItemType] = 'Bug'"
python3 skills/smith/scripts/smith_cli.py board search azdo SRE --query "login error" --state Active
python3 skills/smith/scripts/smith_cli.py board mine azdo SRE
python3 skills/smith/scripts/smith_cli.py board ticket github rtl-devops-gitops 123
python3 skills/smith/scripts/smith_cli.py board search github rtl-devops-gitops --query "retention"
python3 skills/smith/scripts/smith_cli.py board mine github rtl-devops-gitops
python3 skills/smith/scripts/smith_cli.py stories ticket azdo SRE 123456
# GitHub does not support board list in smith v2; use board search instead.
```

## JSON output for automation

```bash
python3 skills/smith/scripts/smith_cli.py code search --query "terraform" --format json
python3 skills/smith/scripts/smith_cli.py code grep github rtl-devops-gitops --pattern "grafana.*" --format json
```

## Re-sync Claude commands after template updates

```bash
bash skills/smith/scripts/install_claude_commands.sh . copy
```
