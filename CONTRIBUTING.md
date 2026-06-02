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

## Releases

The Homebrew formula source of truth is `faustodavid/homebrew-tap/Formula/smith.rb`.
When a `v*` tag is pushed in this repo, the Homebrew Tap Formula workflow updates
that tap formula's tag and revision from the release tag. The workflow requires a
`HOMEBREW_TAP_TOKEN` secret with write access to `faustodavid/homebrew-tap`.

## Contract Stability

- Keep CLI flags, positional args, and exit codes stable.
- Preserve JSON envelope and text renderer contracts.
