<div align="center">

# Smith

**The read-only code investigation CLI for AI coding agents.**

Cross-repository code search, regex grep, pull request inspection, CI/CD log analysis, and work-item lookup across **GitHub**, **GitLab**, **Azure DevOps**, and **YouTrack** — from a single command, with token-efficient output designed for AI Agents.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org)
[![CI](https://github.com/faustodavid/smith/actions/workflows/ci.yml/badge.svg)](https://github.com/faustodavid/smith/actions/workflows/ci.yml)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange.svg)](https://github.com/faustodavid/smith)

<img src="assets/smith_diagram_overview.jpg" alt="Smith — cross-provider read-only code investigation CLI for AI agents" width="700" />

</div>

---

Smith turns every repo search, regex grep, PR review, pipeline log scan, and issue lookup into one CLI surface that AI agents can drive safely — every operation is read-only and every response is trimmed to the smallest useful slice, so your model stays on-task instead of drowning in context.

## Contents

- [Why Smith](#why-smith)
- [Example use cases](#example-use-cases)
- [How Smith solves it](#how-smith-solves-it)
- [What makes Smith different](#what-makes-smith-different)
- [Supported providers](#supported-providers)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Use with your AI editor](#use-with-your-ai-editor)
- [CLI reference](#cli-reference)
- [Quality gates and benchmarks](#quality-gates-and-benchmarks)
- [FAQ](#faq)
- [License](#license)

## Why Smith

AI coding agents need to investigate code across repositories and multiple remotes. The existing approach — provider-specific MCPs like the GitHub MCP — wasn't designed for this.

**Reading files is expensive.** Provider MCPs expose `get_file_contents`, which downloads the entire file. A Helm values file can be 2,000+ lines, but you only need the six lines under `resources.limits`. That's thousands of wasted tokens per read, and in an agentic loop, the agent may read dozens of files per investigation.

**There's no grep.** Without server-side regex, agents have to download a file, scan it themselves, and move on — burning context window on content that doesn't match. Multiply that across files and repos, and a simple question like *"where is the CPU limit set?"* becomes a slow, expensive sequence of full file reads.

**Cross-platform investigations don't exist.** If the answer spans a GitHub repo, a GitLab pipeline, and a YouTrack ticket, the agent needs three different tools with three different interfaces. Most MCPs only cover one provider.

## Example use cases

No cloning, no local checkouts — Smith queries every configured remote through provider APIs, so your agent can investigate across thousands of repos. Load the skill and ask questions like:

**Find how something is configured across every remote**
- Where do we set the CPU limit for the `grafana-agent`?
- What's the default HTTP client timeout in `api-service`?
- Implement this internal package: `<internal_package_link>`

**Audit versions and dependencies across your fleet**
- Which repos still pin `python==3.10`?
- Which repos depend on `acme-telemetry-sdk` v1?

**Investigate PRs, pipelines, and stories**
- Create an implementation plan for `<story_link>`
- Why is this pipeline failing: `<pipeline_link>`?
- Has anyone fixed `PPQ-1055` yet? Link me to the PR
- Implement the review suggestions from `<pr_link>`

> **Want to try this in your editor?** Load the skill (see [Use with your AI editor](#use-with-your-ai-editor)), install Smith, and start asking questions :)

## How Smith solves it

### Cross-provider code search in one command

Start broad with a content search across every configured remote. Smith fans out in parallel and returns compact `repo:/path` pointers, so the agent instantly knows where to drill in.

```bash
$ smith code search "auth middleware"
[github-public] matches: 3
infra-helm/src/auth/middleware.go
api-service/src/auth/middleware.ts
web-app/src/auth/middleware.rs

[gitlab-platform] matches: 2
acme/platform/api/src/auth/middleware.go
acme/platform/web/src/auth/middleware.ts

[gitlab-internal] matches: 3
acme/internal/auth/handler.go
acme/internal/services/authentication.rs
acme/internal/lib/auth.ts
```

This gives the model clear direction for the next step: targeted grep in the right repos.

### Grep instead of read

Smith implements `code grep` across **every provider** — GitHub, GitLab, and Azure DevOps — with regex, path scoping, glob filters, context lines, and line ranges.

```bash
smith github-public code grep infra-helm "resources:" --path charts/ --glob "*.yaml" --context-lines 5
```

Smith returns only the matching lines with surrounding context. The agent gets exactly what it needs in a fraction of the tokens — without cloning, without shelling out to `rg`, and without flooding the context window with unrelated file content.

### Grep pipeline and build logs too

The same grep workflow extends to CI/CD. Instead of downloading an entire build log, Smith lets agents search across all jobs or target a specific one:

```bash
smith github-public pipelines grep my-repo 12345 "error|fatal" --context-lines 3
smith azdo-main pipelines grep SRE 6789 "timeout" --log-id 42
```

### Structured, agent-friendly output

Every command takes `--format json` and emits a predictable envelope (`ok`, `command`, `meta`, `data`, `error`). Agents can parse results without scraping text, and the text renderer is already compact enough for direct prompting.

```bash
smith code search "auth middleware" --format json
smith github-public code grep my-repo "TODO" --format json
```

## What makes Smith different

- **Built for AI, not humans.** Every command is designed around the token budget of an LLM — terse output, regex grep instead of full-file reads, JSON envelopes for parsing.
- **One CLI, every provider.** GitHub, GitLab, Azure DevOps, and YouTrack share the same verbs (`code`, `prs`, `pipelines`, `stories`). No switching between three or four provider MCPs.
- **Read-only by contract.** Smith has no `create`, `update`, `approve`, `comment`, or `post` surface. Agents cannot accidentally mutate production.
- **Local credentials, no hosted service.** Tokens live in your OS keychain / env; nothing is proxied through a third-party server.
- **Parallel cross-remote fan-out.** `smith code search` and `smith prs search` hit every enabled remote concurrently and merge results per remote.
- **Pipeline log grep.** Search CI/CD logs with regex and context windows — a feature most provider MCPs don't ship at all.
- **Deterministic skill.** `skills/smith/SKILL.md` is a structured prompt that tells any LLM exactly when to use Smith, which command shape to pick, and how to recover from 401/429/truncation.
- **MIT-licensed, greenfield.** Alpha, honest about it, and open to breaking changes that improve the contract.

---

## Supported providers

| Provider | Code Search | Code Grep | PRs / MRs | Pipelines | Issues / Stories | Discovery |
|:---------|:-----------:|:---------:|:---------:|:---------:|:----------------:|:---------:|
| **GitHub** | ✅ | ✅ | ✅ | ✅ | ✅ | orgs, repos |
| **GitLab** | ✅ | ✅ | ✅ | ✅ | ✅ | groups, repos |
| **Azure DevOps** | ✅ | ✅ | ✅ | ✅ | ✅ | orgs, repos |
| **YouTrack** | — | — | — | — | ✅ | — |

---

## Installation

### Prerequisites

- **git**
- **[uv](https://docs.astral.sh/uv/)**

### Install from GitHub

**macOS / Linux**:

```bash
curl -sSL https://raw.githubusercontent.com/faustodavid/smith/main/scripts/install.py | python3
```

**Windows (PowerShell)**:

```powershell
irm https://raw.githubusercontent.com/faustodavid/smith/main/scripts/install.py | python -
```

### Install from a local clone

```bash
python3 scripts/install.py
```

The installer keeps a managed Smith repo checkout at `~/.local/share/smith`, mirrors `skills/smith` into `~/.agents/skills/smith`, and installs the `smith` CLI with `uv` from the managed repo checkout.

### Update

```bash
python3 ~/.local/share/smith/scripts/install.py
```

### Verify

```bash
smith --help
```

The installer runs `uv tool update-shell` for you, but you may need to **restart your shell** (or open a new terminal) for PATH changes to take effect — especially on Windows, where `uv` writes the update to the user PATH in the registry.

---

## Quick start

### 1. Initialize configuration

```bash
smith config init            # interactive wizard, or pick manual edit
```

Prefer to tweak remotes later without hand-editing YAML? Use:

```bash
smith config edit            # interactive add / edit / remove
```

### 2. Inspect or edit the config file directly

```bash
smith config path            # prints the path (~/.config/smith/config.yaml)
$EDITOR ~/.config/smith/config.yaml
```

Each remote gets a user-chosen name (like `github-public` or `azdo-main`) that you use as the first argument in provider-scoped commands:

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
    org: acme/platform
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

Override the config path with `SMITH_CONFIG=/path/to/config.yaml` (handy for per-workspace configs or CI).

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
    org: platform/backend
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

Manage remotes without editing the file:

```bash
smith config list                  # list all remotes and their status
smith config show github-public    # show details for one remote
smith config enable azdo-main      # enable a disabled remote
smith config disable azdo-main     # disable a remote without removing it
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
smith code search "auth middleware" --format json          # machine-readable output
```

---

## Use with your AI editor

Smith was built for AI agents from the ground up. `skills/smith/SKILL.md` is a structured prompt document that gives any LLM-powered editor the full playbook:

- **Trigger decision** — when to reach for Smith vs. other tools.
- **Complete command vocabulary** — every valid CLI path with the correct argument shape per provider.
- **Investigation algorithm** — a deterministic broad-to-narrow workflow: discover scope → locate with search → extract proof with grep → corroborate with PRs / pipelines / stories → report with citations.
- **Failure recovery** — specific handlers for 401 / 403, 429, truncation, empty results, and wrong-repo misses.
- **Answer contract** — evidence-first format with exact path citations and a `Sources` section.

The installer mirrors the canonical skill into `~/.agents/skills/smith` and keeps the managed repo checkout at `~/.local/share/smith`. 

---

## CLI reference

Every command accepts `--format {text,json}` (default `text`). JSON responses follow the envelope `{ok, command, meta, data, error}`.

### Global commands

These work across all enabled remotes at once:

```bash
smith code search "<query>"                     # search code across every enabled remote
smith prs search "<query>"                      # search pull requests across every enabled remote
smith config <init|edit|path|list|show|enable|disable>
smith cache clean [--remote <name>|--remote all]
```

Global `smith code search` and `smith prs search` hit every enabled remote in parallel and reject `--project` / `--repo` — use `smith <remote> ...` to narrow.

### Remote-scoped commands

Prefix any command with a configured remote name to target a single provider:

#### Discovery

```bash
smith <remote> repos                            # list repositories
smith <remote> repos <project|group>            # scope by Azure DevOps project or GitLab group
smith <remote> orgs                             # list orgs/projects (GitHub, Azure DevOps)
smith <remote> groups                           # list groups (GitLab only)
```

GitLab `groups` and `repos` support `--grep <regex>`, `--skip`, and `--take` (default `50`, hard max `500`), and surface a truncation notice when there are more matches.

#### Code

```bash
smith <remote> code search "<query>"            # search code in one remote
smith <remote> code grep <repo> "<regex>"       # targeted grep in a repository
```

`code search` supports `--repo` (repeatable) and, for Azure DevOps, `--project`. `code grep` supports `--path`, `--glob`, `--branch`, `--output-mode {content,files_with_matches,count}`, `--context-lines`, `--from-line`, `--to-line`, `--reverse` , `--case-sensitive`, and `--no-clone` (fetch through provider APIs instead of checking out locally).

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
smith <remote> pipelines list <repo> <id>       # list a pipeline + downstream pipelines (GitLab recurses)
smith <remote> pipelines grep <repo> <id> "<regex>"  # grep pipeline logs
```

`pipelines list` supports `--grep`, `--status` (CSV), `--skip`, and `--take`. GitLab additionally supports `--max-depth` (0 = unlimited) and traverses downstream children via the `/bridges` API; GitHub and Azure DevOps return only the root run/build.

`pipelines grep` supports `--log-id`, `--output-mode`, `--context-lines`, `--from-line`, `--to-line`, `--reverse`, and `--case-sensitive`.

#### Stories & Issues

```bash
smith <remote> stories get <id>                 # get a work item / issue
smith <remote> stories search --query "<text>"  # search issues
smith <remote> stories mine                     # list my assigned items
```

`stories search` supports `--area` (except YouTrack), `--type`, `--state`, `--assigned-to`, `--skip`, and `--take`. `stories mine` supports `--include-closed`, `--skip`, and `--take`.

### Provider-specific argument shapes

| Provider | Repo argument | Example |
|:---------|:-------------|:--------|
| **GitHub** | bare `<repo>` (no org prefix) | `smith gh code grep my-repo "TODO"` |
| **GitLab** | full `group/project` path | `smith gl code grep acme/platform/api "TODO"` |
| **Azure DevOps** | `<project> <repo>` | `smith azdo code grep SRE my-repo "TODO"` |
| **YouTrack** | no repo — only issue IDs and search | `smith yt stories get RAD-1055` |

---

## Quality gates and benchmarks

Smith keeps three separate quality layers:

- `scripts/validate_skill_quality.py`
  - Validates the skill contract encoded in `skills/smith/SKILL.md`, `references/*`, and `tests/skills/smith/fixtures/*`.
- `scripts/run_skill_benchmark.py`
  - Runs capability evals against representative investigation tasks defined in `benchmarks/evals/smith_skill_cases.json`.
- `scripts/run_runtime_benchmark.py`
  - Measures CLI performance on fixed runtime scenarios defined in `benchmarks/runtime/scenarios.json`.

```bash
uv sync --extra bench

# Validate the skill contract/docs
uv run python scripts/validate_skill_quality.py --mode all

# Provide auth for capability benchmarks
export GITHUB_TOKEN="ghp_..."          # optional if `gh auth login` is configured
export OPENAI_API_KEY="sk-..."         # only for --executor openai
codex login                            # only for --executor codex

# Run capability evals with different executors
uv run python scripts/run_skill_benchmark.py --executor openai --model gpt-5 --runs 1
uv run python scripts/run_skill_benchmark.py --executor copilot --model gpt-5.4 --runs 1
uv run python scripts/run_skill_benchmark.py --executor codex --model gpt-5.4 --runs 1

# Run runtime/performance benchmarks
uv run python scripts/run_runtime_benchmark.py --runs 3 --write-json benchmarks/runtime/baselines/local.json
```

Capability benchmark outputs land in `benchmarks/workspaces/<timestamp>/` and include `benchmark.json`, `benchmark.md`, per-run transcripts, timing data, grading artifacts, and auditable tool traces.

Checked-in benchmark assets live under `benchmarks/evals/` and `benchmarks/runtime/`. Generated capability benchmark outputs stay under `benchmarks/workspaces/`, which is gitignored.

The Codex capability benchmark executor creates an isolated `CODEX_HOME` and copies your `auth.json` from `~/.codex` (or `CODEX_AUTH_HOME`) so it never modifies your real desktop configuration.

---

## FAQ

### How is Smith different from the GitHub MCP, GitLab MCP, or Azure DevOps MCP?

Provider MCPs expose a `get_file_contents`-style surface that downloads entire files. For an agent trying to answer *"where is the CPU limit set?"*, that means loading thousands of tokens of YAML, scanning, throwing it away, and repeating. Smith ships **server-side regex grep with context lines** across every provider, so the agent gets only the matching window. Smith also fans out across GitHub, GitLab, Azure DevOps, and YouTrack from a single command — a single provider MCP cannot.

### How is Smith different from `ripgrep`, Sourcegraph, or `grep.app`?

`ripgrep` is local-only and requires you to clone every repo first. Sourcegraph is powerful but is a hosted service with its own indexing pipeline and licensing. `grep.app` is read-only public search, not authenticated enterprise search. Smith hits your authenticated GitHub / GitLab / Azure DevOps APIs directly, runs on your machine with your credentials, and is purpose-built for LLM agents.

### Does Smith work with Claude Code, Cursor, Windsurf, GitHub Copilot, and Codex?

Yes. Smith ships a structured skill document (`skills/smith/SKILL.md`) that any LLM-powered editor can load as a rule / instruction / skill. The installer mirrors the canonical skill into `~/.agents/skills/smith`. See [Use with your AI editor](#use-with-your-ai-editor) for per-editor hints.

### Is Smith read-only? Can an agent accidentally push or comment?

Smith is read-only by contract. There are no `create`, `update`, `approve`, `comment`, or `post` commands in the CLI surface. If you want write operations, use `gh`, `glab`, `az`, or `jira` directly — Smith deliberately does not ship them.

---

## License

[MIT](LICENSE)
