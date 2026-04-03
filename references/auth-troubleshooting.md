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

## Missing `GITLAB_GROUP`

```bash
export GITLAB_GROUP="<group>"
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

## GitLab Auth Failure

```bash
export GITLAB_TOKEN="<token>"
# or
glab auth login
glab auth status
```

## 401 Or 403 After Login

- Confirm your account can access the target org, project, or repo.
- Validate the Azure DevOps org URL: `https://dev.azure.com/<org>`.
- Validate the GitLab group path configured in `GITLAB_GROUP`.
- Retry once.
