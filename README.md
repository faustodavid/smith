<div align="center">

# Smith

**The investigation CLI built for AI agents.**

One tool to search code, grep files, inspect PRs, read pipelines, and track issues across GitHub, GitLab, Azure DevOps, and YouTrack — token-efficient, read-only, and agent-ready.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org)

</div>

---

## The Problem

AI coding agents need to investigate code across repositories. The existing approach — provider-specific MCPs like the GitHub MCP — wasn't designed for this.

**Reading files is expensive.** Provider MCPs expose `get_file_contents`, which downloads the entire file. A Helm values file can be 2,000+ lines, but you only need the six lines under `resources.limits`. That's thousands of wasted tokens per read, and in an agentic loop, the agent may read dozens of files per investigation.

**There's no grep.** Without server-side regex, agents have to download a file, scan it themselves, and move on — burning context window on content that doesn't match. Multiply that across files and repos, and a simple question like *"where is the CPU limit set?"* becomes a slow, expensive sequence of full file reads.

**Cross-platform investigations don't exist.** If the answer spans a GitHub repo, a GitLab pipeline, and a YouTrack ticket, the agent needs three different tools with three different interfaces. Most MCPs only cover one provider.

## How Smith Solves It

Smith is a single CLI that replaces bloated provider MCPs for read operations. Every command is optimized to return **the smallest useful result** rather than the full raw API payload.

### Grep instead of read

Smith implements `code grep` across **every provider** — GitHub, GitLab, and Azure DevOps — with regex, path scoping, glob filters, context lines, and line ranges. Instead of downloading a 2,000-line Helm chart, the agent runs:

```bash
smith github-public code grep infra-helm "resources:" --path charts/ --glob "*.yaml" --context-lines 5
```

Smith returns just the matching lines with surrounding context. The agent gets exactly what it needs in a fraction of the tokens.

### Compact search results

`smith code search` returns `repo:/path` pointers, without any extra metadata to keep the result set compact. The agent sees where matches live across every configured remote, then drills into only the relevant files. A single search can fan out to GitHub, GitLab, and Azure DevOps in parallel and return a unified result set.

### Grep pipeline logs too

The same grep workflow extends to CI/CD. Instead of downloading an entire build log, Smith lets agents search across all jobs or target a specific one:

```bash
smith github-public pipelines logs grep my-repo 12345 "error|fatal" --context-lines 3
smith azdo-main pipelines logs grep SRE 6789 "timeout" --log-id 42
```

### Local clone cache

For repos that get grepped repeatedly, Smith shallow-clones the repo once and runs `git grep` locally on subsequent calls. This is dramatically faster than fetching files through the API on every invocation, and the cache auto-refreshes when it goes stale.

### Built for agents, usable by humans

Smith ships with `SKILL.md` — a structured prompt document that teaches LLM-powered editors how to drive Smith autonomously. Register it as a skill in Windsurf, Copilot, Codex, or Claude Code, and the agent learns the full command vocabulary, the broad-to-narrow investigation algorithm, failure recovery, and the answer format — no prompt engineering required.

Every command also supports `--format json` for machine consumption alongside the default human-readable text output.

---

## Supported Providers

| Provider | Code Search | Code Grep | PRs / MRs | Pipelines | Issues / Stories | Discovery |
|:---------|:-----------:|:---------:|:---------:|:---------:|:----------------:|:---------:|
| **GitHub** | ✅ | ✅ | ✅ | ✅ | ✅ | orgs, repos |
| **GitLab** | ✅ | ✅ | ✅ | ✅ | ✅ | groups, repos |
| **Azure DevOps** | ✅ | ✅ | ✅ | ✅ | ✅ | orgs, repos |
| **YouTrack** | — | — | — | — | ✅ | — |

---

## Installation

### Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** (recommended) or pip

### Install with uv

```bash
uv tool install --editable .
```

This places the `smith` executable in `~/.local/bin`. If your shell doesn't find it, run:

```bash
uv tool update-shell
```

### Verify

```bash
smith --help
```

---

## Quick Start

### 1. Initialize configuration

```bash
smith config init
```

### 2. Edit the config file

```bash
smith config path          # prints the path (~/.config/smith/config.yaml)
$EDITOR ~/.config/smith/config.yaml
```

### 3. Set up authentication

Smith reads tokens from environment variables. Set them for your session or, better, store them in your OS keychain so they persist securely:

```bash
# macOS — store tokens in Keychain and load them automatically
security add-generic-password -a "$USER" -s "GITHUB_TOKEN" -w "ghp_..."
security add-generic-password -a "$USER" -s "GITLAB_TOKEN" -w "glpat-..."
security add-generic-password -a "$USER" -s "YOUTRACK_TOKEN" -w "perm:..."
```

Then add these lines to your shell profile (`~/.zshrc`, `~/.bashrc`, etc.):

```bash
export GITHUB_TOKEN=$(security find-generic-password -a "$USER" -s "GITHUB_TOKEN" -w 2>/dev/null)
export GITLAB_TOKEN=$(security find-generic-password -a "$USER" -s "GITLAB_TOKEN" -w 2>/dev/null)
export YOUTRACK_TOKEN=$(security find-generic-password -a "$USER" -s "YOUTRACK_TOKEN" -w 2>/dev/null)
```

Or just export them directly if you prefer:

```bash
export GITHUB_TOKEN="ghp_..."
export GITLAB_TOKEN="glpat-..."
export YOUTRACK_TOKEN="perm:..."
```

For Azure DevOps, authenticate with the Azure CLI:

```bash
az login
```

> **Tip:** On Linux, use `secret-tool` (libsecret) or `pass` instead of `security`. On Windows, use Credential Manager. The key idea is the same: keep tokens out of dotfiles and let your OS manage the secrets.

### 4. Start investigating

```bash
smith config list                                          # verify your remotes
smith code search "grafana"                                # search across all remotes
smith github-public code grep my-repo "TODO" --path src    # targeted grep
smith gitlab-platform repos --grep "^platform/"            # discover repos
smith youtrack-main stories search --query "patch rollout" # find issues
```

---

## Configuration

Smith reads remotes from `~/.config/smith/config.yaml`. Override the path with `SMITH_CONFIG=/path/to/config.yaml`.

Each remote gets a user-chosen name (like `github-public` or `azdo-main`) that you use as the first argument in provider-scoped commands.

### Example config

```yaml
defaults:
  timeout_seconds: 30
  max_output_chars: 20000

remotes:
  github-public:
    provider: github
    org: acme
    token_env: GITHUB_TOKEN
    enabled: true

  gitlab-platform:
    provider: gitlab
    group: acme/platform
    token_env: GITLAB_TOKEN
    enabled: true

  azdo-main:
    provider: azdo
    org: acme
    enabled: true

  youtrack-main:
    provider: youtrack
    host: https://youtrack.acme.com
    token_env: YOUTRACK_TOKEN
    enabled: true
```

### Self-hosted instances

For GitHub Enterprise, self-hosted GitLab, or YouTrack on a custom domain, add `host`:

```yaml
remotes:
  github-enterprise:
    provider: github
    org: platform
    host: github.acme.internal
    token_env: GITHUB_ENTERPRISE_TOKEN
    enabled: true

  gitlab-self-hosted:
    provider: gitlab
    group: platform/backend
    host: gitlab.acme.internal
    token_env: GITLAB_SELF_HOSTED_TOKEN
    enabled: true

  youtrack-self-hosted:
    provider: youtrack
    host: https://youtrack.acme.internal
    token_env: YOUTRACK_TOKEN
    enabled: true
```

> **YouTrack note:** Set `host` to the service root URL. Smith appends `/api` automatically. If your instance is mounted under a subpath, use the full base URL.

### Managing remotes

```bash
smith config list                  # list all remotes and their status
smith config show github-public    # show details for one remote
smith config enable azdo-main      # enable a disabled remote
smith config disable azdo-main     # disable a remote without removing it
```

---

## CLI Reference

### Global commands

These work across all enabled remotes at once:

```bash
smith code search "<query>"                     # search code across every enabled remote
smith prs search "<query>"                      # search pull requests across every enabled remote
smith config <init|path|list|show|enable|disable>
smith cache clean [--remote <name>|--remote all]
```

### Remote-scoped commands

Prefix any command with a configured remote name to target a single provider:

#### Discovery

```bash
smith <remote> repos                            # list repositories
smith <remote> orgs                             # list orgs/projects (GitHub, Azure DevOps)
smith <remote> groups                           # list groups (GitLab only)
```

GitLab discovery supports `--grep`, `--skip`, and `--take` (default `50`, max `500`).

#### Code

```bash
smith <remote> code search "<query>"            # search code in one remote
smith <remote> code grep <repo> "<regex>"       # targeted grep in a repository
```

`code grep` supports `--path`, `--glob`, `--branch`, `--output-mode`, `--context-lines`, `--from-line`, `--to-line`, `--case-sensitive`, and `--no-clone`.

#### Pull Requests / Merge Requests

```bash
smith <remote> prs search "<query>"             # search PRs in one remote
smith <remote> prs list <repo>                  # list PRs
smith <remote> prs get <repo> <id>              # get PR details
smith <remote> prs threads <repo> <id>          # get review comments
```

`prs search` and `prs list` support `--status`, `--creator`, `--date-from`, `--date-to`, `--skip`, `--take`, `--exclude-drafts`, and `--include-labels`.

#### Pipelines

```bash
smith <remote> pipelines logs list <repo> <id>  # list logs for a pipeline run
smith <remote> pipelines logs grep <repo> <id> "<regex>"  # grep pipeline logs
```

`logs grep` supports `--log-id`, `--output-mode`, `--context-lines`, `--from-line`, `--to-line`, and `--case-sensitive`.

#### Stories & Issues

```bash
smith <remote> stories get <id>                 # get a work item / issue
smith <remote> stories search --query "<text>"  # search issues
smith <remote> stories mine                     # list my assigned items
```

`stories search` supports `--area` (except YouTrack), `--type`, `--state`, `--assigned-to`, `--skip`, and `--take`.

### Provider-specific argument shapes

| Provider | Repo argument | Example |
|:---------|:-------------|:--------|
| **GitHub** | bare `<repo>` (no org prefix) | `smith gh code grep my-repo "TODO"` |
| **GitLab** | full `group/project` path | `smith gl code grep acme/platform/api "TODO"` |
| **Azure DevOps** | `<project> <repo>` | `smith azdo code grep SRE my-repo "TODO"` |
| **YouTrack** | no repo — only issue IDs and search | `smith yt stories get RAD-1055` |

---

## AI Skill

Smith was built for AI agents from the ground up. The `SKILL.md` file is a structured prompt document that gives any LLM-powered editor the full playbook:

- **Trigger decision** — when to reach for Smith vs. other tools
- **Complete command vocabulary** — every valid CLI path with correct argument shapes per provider
- **Investigation algorithm** — a deterministic broad-to-narrow workflow: discover scope → locate with search → extract proof with grep → corroborate with PRs/pipelines/stories → report with citations
- **Failure recovery** — specific handlers for 401/403, 429, truncation, empty results, and wrong-repo misses
- **Answer contract** — evidence-first format with exact path citations and a `Sources` section

Register `SKILL.md` as a skill in your editor — Windsurf, GitHub Copilot, Codex, Claude Code — and the agent drives Smith commands autonomously. No prompt engineering needed.

---

## Development

```bash
uv sync --extra dev
uv run ruff check .
uv run mypy src
uv run pytest -q
```

`pytest -q` runs the fast `unit` and `contract` tiers. Live integration tests are skipped by default.

To run integration smoke tests (requires credentials and fixture IDs):

```bash
uv run pytest tests/integration -q --run-integration
```

### Environment variables for integration tests

| Provider | Variables |
|:---------|:---------|
| **GitHub** | `GITHUB_ORG`, `GITHUB_TOKEN`, `SMITH_TEST_GITHUB_REPO`, `SMITH_TEST_GITHUB_PR_ID`, `SMITH_TEST_GITHUB_RUN_ID`, `SMITH_TEST_GITHUB_ISSUE_ID` |
| **GitLab** | `GITLAB_GROUP`, `GITLAB_TOKEN`, `SMITH_TEST_GITLAB_REPO`, `SMITH_TEST_GITLAB_MR_ID`, `SMITH_TEST_GITLAB_PIPELINE_ID`, `SMITH_TEST_GITLAB_ISSUE_ID` |
| **Azure DevOps** | `AZURE_DEVOPS_ORG`, `SMITH_TEST_AZDO_PROJECT`, `SMITH_TEST_AZDO_REPO`, `SMITH_TEST_AZDO_PR_ID`, `SMITH_TEST_AZDO_BUILD_ID`, `SMITH_TEST_AZDO_WORK_ITEM_ID` |

---

## Benchmarks

Smith includes an automated skill benchmark that evaluates how well AI agents use Smith on real investigation scenarios.

```bash
uv sync --extra bench

# Provide auth
export GITHUB_TOKEN="ghp_..."          # optional if `gh auth login` is configured
export OPENAI_API_KEY="sk-..."         # only for --executor openai
codex login                            # only for --executor codex

# Run with different executors
uv run python scripts/run_skill_benchmark.py --executor openai --model gpt-5 --runs 1
uv run python scripts/run_skill_benchmark.py --executor copilot --model gpt-5.4 --runs 1
uv run python scripts/run_skill_benchmark.py --executor codex --model gpt-5.4 --runs 1
```

Benchmark outputs land in `benchmarks/workspaces/<timestamp>/` and include `benchmark.json`, `benchmark.md`, per-run transcripts, timing data, grading artifacts, and auditable tool traces.

The Codex executor creates an isolated `CODEX_HOME` and copies your `auth.json` from `~/.codex` (or `CODEX_AUTH_HOME`) so it never modifies your real desktop configuration.

---

## License

[MIT](LICENSE)
