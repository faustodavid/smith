# smith

`smith` is a read-only investigation CLI for Azure DevOps and GitHub.

## Goals

- Deterministic read-only investigation workflows.
- Installable Python package (`smith` console entrypoint).

## Install

```bash
python -m pip install .
```

## Quick Start

```bash
export AZURE_DEVOPS_ORG="<org>"
export GITHUB_ORG="<org>"
smith code search "grafana" --provider all
```

## Development

```bash
python -m pip install -e .[dev]
ruff check .
mypy src
pytest -q
```

`pytest -q` runs the fast `unit` and `contract` tiers and skips live integration smoke tests by default.

Run the live provider smoke lane explicitly when credentials and fixture IDs are available:

```bash
pytest tests/integration -q --run-integration
```

Integration smoke tests read these environment variables when present:

- GitHub: `GITHUB_ORG`, `GITHUB_TOKEN`, `SMITH_TEST_GITHUB_REPO`, `SMITH_TEST_GITHUB_PR_ID`, `SMITH_TEST_GITHUB_RUN_ID`, `SMITH_TEST_GITHUB_ISSUE_ID`
- Azure DevOps: `AZURE_DEVOPS_ORG`, `SMITH_TEST_AZDO_PROJECT`, `SMITH_TEST_AZDO_REPO`, `SMITH_TEST_AZDO_PR_ID`, `SMITH_TEST_AZDO_BUILD_ID`, `SMITH_TEST_AZDO_WORK_ITEM_ID`

## License

MIT (see `LICENSE`).
