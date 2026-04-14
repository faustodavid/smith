# Auth Troubleshooting

Use this only when env or credentials block progress.

## Missing Remote Configuration

```bash
smith config path
smith config list
smith config show <configured-remote-name>
```

If the needed remote is missing, add it to the active Smith config or point `SMITH_CONFIG` at the correct config file.

## Missing Token Env Var

```bash
printenv GITHUB_TOKEN
printenv GITLAB_TOKEN
printenv AZURE_DEVOPS_PAT
```

Use the token env var named by the remote's `token_env` field.

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
- Validate the GitLab group path configured on the target remote.
- Retry once.
