# Usage Recipes

For non-trivial requests, follow this order first:

1. Read `references/trigger-cases.md`.
2. Read `references/behavioral-quality-gates.md`.
3. Read `references/failure-playbook.md`.
4. Then execute commands from this recipe file.

## Prerequisites

```bash
export AZURE_DEVOPS_ORG_URL="https://dev.azure.com/<org>"
az login
```

## Investigation pattern (QueryAgent-inspired)

1. Broad discovery:
```bash
python3 skills/smith/scripts/smith_cli.py code search --query "<topic keywords>" --take 30
```

2. Structure mapping:
```bash
python3 skills/smith/scripts/smith_cli.py code grep --project <project> --repo <repo> --path / --output-mode files_with_matches --pattern ".*"
```

3. Focused extraction:
```bash
python3 skills/smith/scripts/smith_cli.py code grep --project <project> --repo <repo> --path <path> --glob "<glob>" --pattern "<regex>" --context-lines 2
```

4. Report with evidence:
- Include exact `project/repository:path` sources.
- If unresolved, state "not enough evidence" and provide the next narrowing command.

## Discover projects and repos

```bash
python3 skills/smith/scripts/smith_cli.py projects list
python3 skills/smith/scripts/smith_cli.py repos list --project SRE
```

## Broad search, then targeted grep

```bash
python3 skills/smith/scripts/smith_cli.py code search --query "grafana AND path:*alerts*"
python3 skills/smith/scripts/smith_cli.py code grep --project SRE --repo rtl-devops-gitops --path /alerts --glob "*.yaml" --pattern "severity" --context-lines 2
```

## Pull request investigation

```bash
python3 skills/smith/scripts/smith_cli.py pr list --project SRE --status active,completed --take 25
python3 skills/smith/scripts/smith_cli.py pr get --project SRE --repo rtl-devops-gitops --id 12345
python3 skills/smith/scripts/smith_cli.py pr threads --project SRE --repo rtl-devops-gitops --id 12345
```

## Build log investigation

```bash
python3 skills/smith/scripts/smith_cli.py build logs --project SRE --id 942510
python3 skills/smith/scripts/smith_cli.py build grep --project SRE --id 942510 --pattern "ERROR|Exception" --output-mode logs_with_matches
python3 skills/smith/scripts/smith_cli.py build grep --project SRE --id 942510 --log-id 18 --from-line 380
```

## Board read workflows

```bash
python3 skills/smith/scripts/smith_cli.py board ticket --project SRE --id 123456
python3 skills/smith/scripts/smith_cli.py board list --project SRE --wiql "Select [System.Id], [System.Title] From WorkItems Where [System.WorkItemType] = 'Bug'"
python3 skills/smith/scripts/smith_cli.py board search --query "login error" --project SRE --state Active
python3 skills/smith/scripts/smith_cli.py board mine --project SRE
```

## JSON output for automation

```bash
python3 skills/smith/scripts/smith_cli.py code search --query "terraform" --format json
```

## Re-sync Claude commands after template updates

```bash
bash skills/smith/scripts/install_claude_commands.sh . copy
```
