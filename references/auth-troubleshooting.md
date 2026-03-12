# Auth Troubleshooting

Use this only when env or credentials block progress.

## Missing `AZURE_DEVOPS_ORG`

```bash
export AZURE_DEVOPS_ORG="<org>"
```

## Missing `GITHUB_ORG`

```bash
export GITHUB_ORG="<org>"
```

## Azure Login Or Token Failure

```bash
az login
az account show
az account set --subscription "<name-or-id>"
```

Retry the command after selecting the right tenant or subscription.

## GitHub Auth Failure

```bash
export GITHUB_TOKEN="<token>"
# or
gh auth login
gh auth status
```

## 401 Or 403 After Login

- Confirm your account can access the target org, project, or repo.
- Validate the Azure DevOps org URL: `https://dev.azure.com/<org>`.
- Retry once.
