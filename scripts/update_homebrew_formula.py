#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PYPROJECT = REPO_ROOT / "pyproject.toml"
PIN_RE = re.compile(
    r'(?P<prefix>url "https://github\.com/faustodavid/smith\.git",\n\s+tag:\s+)"(?P<tag>[^"]+)"'
    r'(?P<middle>,\n\s+revision:\s+)"(?P<revision>[0-9a-f]{40})"',
    re.MULTILINE,
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TAG_RE = re.compile(r"^v[0-9A-Za-z][0-9A-Za-z._-]*$")


class FormulaUpdateError(ValueError):
    pass


def load_project_version(path: Path = DEFAULT_PYPROJECT) -> str:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise FormulaUpdateError(f"project.version is missing from {path}")
    return version


def expected_tag(version: str) -> str:
    return f"v{version}"


def resolve_tag_revision(tag: str, repo_root: Path = REPO_ROOT) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-list", "-n", "1", tag],
            cwd=repo_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() or f"git rev-list could not resolve {tag}"
        raise FormulaUpdateError(stderr) from exc

    revision = result.stdout.strip()
    validate_revision(revision)
    return revision


def validate_revision(revision: str) -> None:
    if not SHA_RE.fullmatch(revision):
        raise FormulaUpdateError(f"revision must be a 40-character lowercase git SHA, got {revision!r}")


def validate_tag(tag: str) -> None:
    if not TAG_RE.fullmatch(tag):
        raise FormulaUpdateError(f"tag must start with v and contain only letters, numbers, dots, underscores, and hyphens, got {tag!r}")


def parse_formula_pin(text: str) -> tuple[str, str]:
    match = PIN_RE.search(text)
    if not match:
        raise FormulaUpdateError("could not find the Smith formula url tag/revision pin")
    return match.group("tag"), match.group("revision")


def update_formula_text(text: str, tag: str, revision: str) -> str:
    validate_tag(tag)
    validate_revision(revision)

    def replace(match: re.Match[str]) -> str:
        return f'{match.group("prefix")}"{tag}"{match.group("middle")}"{revision}"'

    updated, count = PIN_RE.subn(replace, text, count=1)
    if count != 1:
        raise FormulaUpdateError("could not find the Smith formula url tag/revision pin")
    return updated


def update_formula(path: Path, tag: str, revision: str, *, check: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    updated = update_formula_text(text, tag, revision)
    changed = updated != text
    if check and changed:
        current_tag, current_revision = parse_formula_pin(text)
        raise FormulaUpdateError(
            f"{path} pins {current_tag}@{current_revision}; expected {tag}@{revision}. "
            "Run scripts/update_homebrew_formula.py --formula <path> to refresh it."
        )
    if changed:
        path.write_text(updated, encoding="utf-8")
    return changed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Update a Homebrew formula to pin the current release tag and commit.")
    parser.add_argument("--formula", type=Path, required=True, help="Formula path to update, usually homebrew-tap/Formula/smith.rb.")
    parser.add_argument("--pyproject", type=Path, default=DEFAULT_PYPROJECT, help="pyproject.toml path used for the default tag.")
    parser.add_argument("--version", help="Project version to convert to a v-prefixed tag. Defaults to pyproject.toml.")
    parser.add_argument("--tag", help="Release tag to pin. Defaults to v{project.version}.")
    parser.add_argument("--revision", help="Release commit SHA. Defaults to resolving the selected tag with git.")
    parser.add_argument("--check", action="store_true", help="Fail if the formula is not already up to date.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    version = args.version or load_project_version(args.pyproject)
    tag = args.tag or expected_tag(version)
    validate_tag(tag)
    project_tag = expected_tag(version)
    if tag != project_tag:
        raise FormulaUpdateError(f"tag {tag!r} does not match project.version {version!r}; expected {project_tag!r}")

    revision = args.revision or resolve_tag_revision(tag)
    changed = update_formula(args.formula, tag, revision, check=args.check)
    status = "updated" if changed else "already current"
    print(f"{args.formula}: {status} at {tag}@{revision}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FormulaUpdateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
