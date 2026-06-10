<div align="center">

# Smith

**Read-only code investigation for AI coding agents.**

Search code, grep exact lines, inspect PRs, scan CI logs, and look up issues across
**GitHub**, **GitLab**, **Azure DevOps**, and **YouTrack** from one CLI.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org)
[![CI](https://github.com/faustodavid/smith/actions/workflows/ci.yml/badge.svg)](https://github.com/faustodavid/smith/actions/workflows/ci.yml)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange.svg)](https://github.com/faustodavid/smith)

<img src="assets/smith_diagram_overview.jpg" alt="Smith cross-provider read-only investigation CLI for AI agents" width="700" />

</div>

Smith gives an agent a safe, token-efficient way to answer questions like:

- Where is this setting defined?
- Which repos still use this dependency?
- What changed in this PR?
- Why did this pipeline fail?
- Is there an issue or story that explains this work?

It does that without cloning every repo, downloading whole files, or switching
between provider-specific tools.

Think of Smith as a read-only remote investigation layer for AI agents: similar
in spirit to a provider MCP like the GitHub MCP, but optimized for token efficient
cross-remote search, grep-sized evidence, PR context, CI logs, and issue lookup.

## Why Smith Helps

- **Fewer tokens wasted.** Smith returns search hits and grep context windows
  instead of full files.
- **One workflow across providers.** The same verbs work across GitHub, GitLab,
  Azure DevOps, and YouTrack.
- **Read-only by contract.** There are no create, update, approve, comment, or
  post commands.
- **Local credentials.** Tokens stay in your environment, keychain, or provider
  CLI login. Smith is not a hosted proxy.
- **Agent-ready output.** We emit minimalist compact responses optimised for the LLMs.

## Golden Paths

The first two paths are for you: install Smith and connect your remotes. After
that, the examples show the behavior encoded in `skills/smith/SKILL.md` — the
workflow your AI agent should follow once the Smith skill is loaded.

### Install and Connect

#### 1. Install Smith

Recommended for macOS and Linux:

```bash
brew install faustodavid/tap/smith
smith config init
```

Standalone installer:

```bash
curl -sSL https://raw.githubusercontent.com/faustodavid/smith/main/scripts/install.py | python3
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/faustodavid/smith/main/scripts/install.py | python -
```

`smith config init` syncs the Smith agent skill to `~/.agents/skills/smith`.
The standalone installer does this during install too. The skill stays current
on its own after upgrades (set `SMITH_SKILL_CHECK=0` to opt out); to refresh it
manually run:

```bash
smith skill sync
```

#### 2. Add Remotes

`smith config init` starts a guided terminal onboarding flow. It scans for
existing provider auth, helps add remotes, and can store pasted tokens in the OS
secure credential store. Token values are never written to `config.yaml`.

To create an empty config for manual editing, run `smith config init --manual`.
`smith config init --format json` is also noninteractive for automation and
creates the same empty config shape.

To change remotes later, run `smith config edit`. If you prefer manual edits:

```bash
smith config path
$EDITOR ~/.config/smith/config.yaml
```

Minimal config shape:

```yaml
defaults:
  timeout_seconds: 30
  max_output_chars: 20000

remotes:
  github-public:
    provider: github
    org: acme
    enabled: true

  gitlab-platform:
    provider: gitlab
    org: acme/platform
    enabled: true

  azdo-main:
    provider: azdo
    org: acme
    enabled: true

  youtrack-main:
    provider: youtrack
    host: https://youtrack.acme.com
    enabled: true
```

Set `SMITH_CONFIG=/path/to/config.yaml` when a workspace needs a different
config file.

Auth options:

- `smith config show <remote>` prints the persisted remote fields only. If
  `token_env` is absent, Smith may still authenticate through runtime fallback
  sources below.
- `token_env` is optional when a provider has an implicit env fallback or CLI
  login. Add it when you want Smith to read a specific environment variable or
  secure-store entry.
- GitHub: configured env/secure-store token, public GitHub's implicit
  `GITHUB_TOKEN`, then host-scoped `gh auth token`. GitHub Enterprise uses
  host-scoped `gh` auth unless you configure `token_env`.
- GitLab: configured env/secure-store token, implicit `GITLAB_TOKEN`, then host-scoped
  `glab config get token`.
- Azure DevOps: configured `AZURE_DEVOPS_PAT` env/secure-store PAT when
  `token_env` is set, otherwise `az login` / Azure DefaultAzureCredential.
- YouTrack: configured env/secure-store token, or implicit `YOUTRACK_TOKEN`
  when `token_env` is omitted.

### For Your Agent: Skill-Led Investigation

The Smith skill teaches your AI agent a broad-to-narrow investigation loop:
search first, avoid full-file reads, extract proof with grep, then corroborate
with PRs, pipelines, or issues when needed.

#### 3. Search Everywhere, Then Narrow

When you ask an agent where something lives or how something is configured, the
skill should make it start broad across all enabled remotes:

```bash
smith code search "auth middleware" --take 30
smith code search "python==3.10" --glob "pyproject.toml"
```

After search finds likely repos or paths, the skill should make the agent grep
the likely repository for exact proof:

```bash
smith github-public code grep api-service "timeout_seconds" --path src --context-lines 2
smith gitlab-platform code grep acme/platform/api "resources:" --glob "*.yaml" --context-lines 5
smith azdo-main code grep SRE api-service "TODO" --path src
```

For larger areas, the skill can map a subtree before reading matches:

```bash
smith github-public code grep api-service ".*" --path src/auth --output-mode files_with_matches
```

#### 4. Inspect PRs and Review Threads

When your request points at a PR, review, or implementation history, the skill
guides the agent toward PR search, details, and review threads:

```bash
smith prs search "auth middleware" --status active --exclude-drafts
smith github-public prs list api-service --status active
smith github-public prs get api-service 123
smith github-public prs threads api-service 123
```

Provider repo arguments matter. These examples assume remotes named `gh`, `gl`,
`azdo`, and `yt`:

| Provider | Repo argument shape | Example |
|---|---|---|
| GitHub | bare repo name | `smith gh code grep api-service "TODO"` |
| GitLab | full group/project path | `smith gl code grep acme/platform/api "TODO"` |
| Azure DevOps | project then repo | `smith azdo code grep SRE api-service "TODO"` |
| YouTrack | issue IDs and queries only | `smith yt stories get RAD-1055` |

#### 5. Debug a Failing Pipeline

When your request is about a CI failure, the skill tells the agent to list the
run first, then grep logs instead of dumping entire job output:

```bash
smith github-public pipelines list api-service 123456789
smith github-public pipelines grep api-service 123456789 "error|fatal|Traceback" --reverse --context-lines 3
```

Target one job or log when needed:

```bash
smith azdo-main pipelines grep SRE 6789 "timeout" --log-id 42 --context-lines 3
```

#### 6. Find Issues and Stories

When story or issue context is needed, the skill gives the agent one shape for
GitHub, GitLab, Azure DevOps, and YouTrack lookups:

```bash
smith github-public stories search api-service --query "rate limit"
smith gitlab-platform stories search acme/platform/api --query "migration rollback"
smith youtrack-main stories search --query "patch rollout" --state Open
smith youtrack-main stories get RAD-1055
```

## Supported Providers

| Provider | Code search | Code grep | PRs / MRs | Pipelines | Issues / stories | Discovery |
|---|---:|---:|---:|---:|---:|---:|
| GitHub | Yes | Yes | Yes | Yes | Yes | Orgs, repos |
| GitLab | Yes | Yes | Yes | Yes | Yes | Groups, repos |
| Azure DevOps | Yes | Yes | Yes | Yes | Yes | Orgs, repos |
| YouTrack | - | - | - | - | Yes | - |

## Troubleshooting

- `401` or `403`: check `smith config show <remote>` for persisted fields. If
  the remote has `token_env`, set that env var or store a token with
  `smith config edit`. Otherwise check implicit provider env vars where
  supported (`GITHUB_TOKEN`, `GITLAB_TOKEN`, `YOUTRACK_TOKEN`) and refresh
  provider login/status as appropriate: `gh auth login`,
  `glab auth login --hostname <host>`, or `az login`.
- `429`: lower `--take`, narrow `--path` or `--glob`, or reduce GitHub grep
  workers with `GITHUB_GREP_MAX_WORKERS`.
- Truncated output: reduce `--context-lines`, narrow the path, or page with
  `--from-line` and `--to-line`.
- Empty results: broaden the search query, then grep candidate repos again.

## Development

```bash
uv sync --extra dev
make check
```

Useful quality gates:

```bash
uv run --extra dev python scripts/validate_skill_quality.py --mode all
uv run --extra dev pytest tests/unit tests/contract -q
```

## Status

Smith is alpha software. The goal is a stable, read-only investigation contract
for AI agents, but command details may still change while the project hardens.

## License

[MIT](LICENSE)
