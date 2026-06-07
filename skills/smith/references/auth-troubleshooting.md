# Auth Troubleshooting

Open only when env or credentials block progress.

## Missing remote

```bash
smith config path
smith config list
smith config show <remote-name>
```

If the needed remote is missing, run `smith config init` or `smith config edit` to add it. Point `SMITH_CONFIG` at the right file when using a workspace-specific config.

`smith config show <remote-name>` shows persisted fields only. A missing
`token_env` can still be valid when Smith can use an implicit provider env var,
provider CLI token, or provider status login at runtime.

## Missing runtime token

```bash
test -n "${GITHUB_TOKEN:-}" && echo GITHUB_TOKEN=set || echo GITHUB_TOKEN=missing
test -n "${GITLAB_TOKEN:-}" && echo GITLAB_TOKEN=set || echo GITLAB_TOKEN=missing
test -n "${AZURE_DEVOPS_PAT:-}" && echo AZURE_DEVOPS_PAT=set || echo AZURE_DEVOPS_PAT=missing
test -n "${YOUTRACK_TOKEN:-}" && echo YOUTRACK_TOKEN=set || echo YOUTRACK_TOKEN=missing
```

Do not print token values. Runtime auth may check a configured `token_env`, an
implicit provider env var where supported (`GITHUB_TOKEN`, `GITLAB_TOKEN`, or
`YOUTRACK_TOKEN`), and provider CLI/status login where supported. If
`smith config show <remote-name>` lists `token_env`, set that env var or rerun
`smith config edit` and use the secure-store token option for that env key, then
also verify the provider login/status fallback Smith can use at runtime.

## Provider-specific login

```bash
# Azure DevOps
az login
az account show
az account set --subscription "<name-or-id>"

# GitHub
gh auth login
gh auth token >/dev/null

# GitLab
glab auth login --hostname <host>
glab config get token --host <host> >/dev/null

# Provider token env vars, including implicit runtime fallbacks where supported
test -n "${GITHUB_TOKEN:-}" && echo GITHUB_TOKEN=set || echo GITHUB_TOKEN=missing
test -n "${GITLAB_TOKEN:-}" && echo GITLAB_TOKEN=set || echo GITLAB_TOKEN=missing
test -n "${AZURE_DEVOPS_PAT:-}" && echo AZURE_DEVOPS_PAT=set || echo AZURE_DEVOPS_PAT=missing
test -n "${YOUTRACK_TOKEN:-}" && echo YOUTRACK_TOKEN=set || echo YOUTRACK_TOKEN=missing
```

## 401 or 403 after login

- Confirm the account can access the org, project, or repo.
- Validate the Azure DevOps org URL: `https://dev.azure.com/<org>`.
- Validate the GitLab `org` group path configured on the remote.
- Retry once; if it fails, fall back to `references/failure-playbook.md`.
