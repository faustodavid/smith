# Contributing

## Setup

```bash
python -m pip install -e .[dev]
```

## Quality Gates

Run before opening a PR:

```bash
ruff check .
mypy src
pytest -q
```

## Contract Stability

- Keep CLI flags, positional args, and exit codes stable.
- Preserve JSON envelope and text renderer contracts.
