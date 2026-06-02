# Contributing

## Setup

```bash
python3 -m pip install -e '.[dev]'
```

## Quality Gates

Run before opening a PR:

```bash
make check
```

## Contract Stability

- Keep CLI flags, positional args, and exit codes stable.
- Preserve JSON envelope and text renderer contracts.
