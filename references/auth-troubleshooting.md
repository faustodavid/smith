# Auth Troubleshooting

## Symptom: "Missing AZURE_DEVOPS_ORG"

Set the required env var:

```bash
export AZURE_DEVOPS_ORG="<org>"
```

## Symptom: token acquisition failure

1. Run Azure login:

```bash
az login
```

2. If multiple tenants/subscriptions exist, explicitly select context:

```bash
az account show
az account set --subscription "<name-or-id>"
```

3. Retry command.

## Symptom: Missing `GITHUB_ORG`

Set org scope for GitHub provider:

```bash
export GITHUB_ORG="<org>"
```

## Symptom: GitHub auth failure

Preferred:

```bash
export GITHUB_TOKEN="<token>"
```

Or fallback:

```bash
gh auth login
gh auth status
```

## Symptom: HTTP 401/403 after login

- Confirm your account has Azure DevOps access for the target project/org.
- Retry once after a fresh login.
- Validate org URL is correct (`https://dev.azure.com/<org>`).

## Symptom: HTTP 429 rate limiting

- Narrow scope (specific project/repo/path).
- Decrease `--take` values.
- Retry with fewer broad scans.

## Symptom: grep output truncated

- Narrow `--path` and `--glob`.
- Use `--from-line`/`--to-line`.
- Increase `THANOS_LOCAL_MAX_OUTPUT_CHARS` only if needed.

## Symptom: GitHub grep is slow

- Prefer targeted scope: pass `--path`, `--glob`, and explicit `--branch`.
- Keep parallel fetch enabled:
  - `export GITHUB_GREP_ENABLE_PARALLEL=true`
  - `export GITHUB_GREP_MAX_WORKERS=8` (tune between `1` and `32`)
- If hitting rate limits, lower workers or disable parallel temporarily:
  - `export GITHUB_GREP_MAX_WORKERS=1`
  - `export GITHUB_GREP_ENABLE_PARALLEL=false`
