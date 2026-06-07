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
When a `v*` tag is pushed in this repo, the Homebrew Release Validation workflow
checks that the release tag matches the tagged `project.version` (for example,
tag `vX.Y.Z` for `version = "X.Y.Z"`). Formula updates run from the tap repo's
own Update Smith Formula workflow, using that repo's short-lived `GITHUB_TOKEN`;
this repo does not store a cross-repo Homebrew tap token.

## Contract Stability

- Keep CLI flags, positional args, and exit codes stable.
- Preserve JSON envelope and text renderer contracts.
