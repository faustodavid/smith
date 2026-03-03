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

## License

MIT (see `LICENSE`).
