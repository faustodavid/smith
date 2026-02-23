# Auth Troubleshooting

## Symptom: "Missing AZURE_DEVOPS_ORG_URL"

Set the required env var:

```bash
export AZURE_DEVOPS_ORG_URL="https://dev.azure.com/<org>"
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
