# smith

`smith` is a read-only investigation CLI for Azure DevOps, GitHub, and GitLab.

## Goals

- Deterministic read-only investigation workflows.
- Installable Python package (`smith` console entrypoint).

## Install

```bash
uv tool install --editable .
```

`uv tool install` places the `smith` executable in `~/.local/bin`. If a new terminal still cannot find it, run:

```bash
uv tool update-shell
```

## Quick Start

```bash
smith config init
smith config path
$EDITOR ~/.config/smith/config.yaml

export GITHUB_TOKEN="<token>"
export GITLAB_TOKEN="<token>"
az login

smith config list
smith github-public repos
smith gitlab-platform repos
smith code search "grafana"
```

## Configuration

Smith reads remotes from `~/.config/smith/config.yaml` by default. You can override the location with `SMITH_CONFIG=/path/to/config.yaml`.

Commands take configured remote names such as `github-public`, `gitlab-platform`, or `azdo-main`.

Create the file:

```bash
smith config init
```

Example config:

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
```

For self-hosted GitHub Enterprise or GitLab, set `host`:

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
```

Useful config commands:

```bash
smith config path
smith config list
smith config show github-public
smith config disable azdo-main
smith config enable azdo-main
```

Usage examples:

```bash
# Search across every enabled remote
smith code search "grafana"

# Search only one configured remote
smith github-public code search "grafana"

# List repositories for one configured GitLab remote
smith gitlab-platform repos

# Grep a repository through one configured GitHub remote
smith github-public code grep repo-a "TODO"

# Azure DevOps commands still take a project plus repo
smith azdo-main code grep SRE repo-a "timeout"
```

When multiple remotes are queried, Smith labels results with the remote name.

## Development

```bash
uv sync --extra dev
uv run ruff check .
uv run mypy src
uv run pytest -q
```

`pytest -q` runs the fast `unit` and `contract` tiers and skips live integration smoke tests by default.

Run the live provider smoke lane explicitly when credentials and fixture IDs are available:

```bash
uv run pytest tests/integration -q --run-integration
```

## Benchmark

Install the benchmark extras and provide GitHub auth. Use `--executor openai` with an OpenAI API key, `--executor copilot` with a logged-in `copilot` CLI, or `--executor codex` with a logged-in `codex` CLI backed by ChatGPT:

```bash
uv sync --extra bench
export GITHUB_TOKEN="<token>"  # optional if `gh auth login` is already configured
export OPENAI_API_KEY="<token>"  # only for --executor openai
codex login  # only for --executor codex
uv run python scripts/run_skill_benchmark.py --executor openai --model gpt-5 --runs 1
uv run python scripts/run_skill_benchmark.py --executor copilot --model gpt-5.4 --runs 1
uv run python scripts/run_skill_benchmark.py --executor codex --model gpt-5.4 --runs 1
```

The benchmark writes tracked inputs to `evals/evals.json` and generated outputs to `benchmarks/workspaces/<timestamp>/`, including `benchmark.json`, `benchmark.md`, per-run transcripts, timing, grading artifacts, and auditable tool traces in `outputs/tool_trace.json` and `outputs/tool_trace.md`.

The Codex executor creates a scratch `CODEX_HOME`, copies your existing `auth.json` from `~/.codex` (or `CODEX_AUTH_HOME`), and registers the benchmark MCP servers there so it does not modify your real Codex desktop configuration.

Integration smoke tests read these environment variables when present:

- GitHub: `GITHUB_ORG`, `GITHUB_TOKEN`, `SMITH_TEST_GITHUB_REPO`, `SMITH_TEST_GITHUB_PR_ID`, `SMITH_TEST_GITHUB_RUN_ID`, `SMITH_TEST_GITHUB_ISSUE_ID`
- GitLab: `GITLAB_GROUP`, `GITLAB_TOKEN`, `SMITH_TEST_GITLAB_REPO`, `SMITH_TEST_GITLAB_MR_ID`, `SMITH_TEST_GITLAB_PIPELINE_ID`, `SMITH_TEST_GITLAB_ISSUE_ID`
- Azure DevOps: `AZURE_DEVOPS_ORG`, `SMITH_TEST_AZDO_PROJECT`, `SMITH_TEST_AZDO_REPO`, `SMITH_TEST_AZDO_PR_ID`, `SMITH_TEST_AZDO_BUILD_ID`, `SMITH_TEST_AZDO_WORK_ITEM_ID`

## License

MIT (see `LICENSE`).
