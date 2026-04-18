# Auth Troubleshooting

Open only when env or credentials block progress.

## Missing remote

```bash
smith config path
smith config list
smith config show <remote-name>
```

If the needed remote is missing, add it to the active config or point `SMITH_CONFIG` at the right file.

## Missing token env var

```bash
printenv GITHUB_TOKEN
printenv GITLAB_TOKEN
printenv AZURE_DEVOPS_PAT
printenv YOUTRACK_TOKEN
```

Set the env var named by the remote's `token_env` field.

## Provider-specific login

```bash
# Azure DevOps
az login
az account show
az account set --subscription "<name-or-id>"

# GitHub
export GITHUB_TOKEN="<token>"   # or: gh auth login && gh auth status

# GitLab
export GITLAB_TOKEN="<token>"   # or: glab auth login && glab auth status
```

## 401 or 403 after login

- Confirm the account can access the org, project, or repo.
- Validate the Azure DevOps org URL: `https://dev.azure.com/<org>`.
- Validate the GitLab `group` path configured on the remote.
- Retry once; if it fails, fall back to `references/failure-playbook.md`.
