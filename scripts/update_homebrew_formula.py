#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
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
CAVEATS_RE = re.compile(r"\n+\s+def caveats\n\s+<<~EOS\n.*?\n\s+EOS\n\s+end", re.DOTALL)
CAVEATS_METHOD = """\
  def caveats
    <<~EOS
      Smith is installed.

      Start the guided setup:
        smith config init

      This creates your config, links the Smith agent skill, and helps add
      GitHub, GitLab, Azure DevOps, and YouTrack remotes securely.
    EOS
  end
""".rstrip()


class FormulaUpdateError(ValueError):
    pass


@dataclass(frozen=True)
class FormulaUpdate:
    text: str
    pin_changed: bool
    caveats_changed: bool

    @property
    def changed(self) -> bool:
        return self.pin_changed or self.caveats_changed


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


def update_formula_pin_text(text: str, tag: str, revision: str) -> str:
    validate_tag(tag)
    validate_revision(revision)

    def replace(match: re.Match[str]) -> str:
        return f'{match.group("prefix")}"{tag}"{match.group("middle")}"{revision}"'

    updated, count = PIN_RE.subn(replace, text, count=1)
    if count != 1:
        raise FormulaUpdateError("could not find the Smith formula url tag/revision pin")
    return updated


def prepare_formula_update(text: str, tag: str, revision: str) -> FormulaUpdate:
    pin_updated = update_formula_pin_text(text, tag, revision)
    caveats_updated = ensure_formula_caveats(pin_updated)
    return FormulaUpdate(
        text=caveats_updated,
        pin_changed=pin_updated != text,
        caveats_changed=caveats_updated != pin_updated,
    )


def update_formula_text(text: str, tag: str, revision: str) -> str:
    return prepare_formula_update(text, tag, revision).text


def ensure_formula_caveats(text: str) -> str:
    match = CAVEATS_RE.search(text)
    if match:
        return _join_with_caveats(text[: match.start()], text[match.end() :])

    test_index = text.find("\n  test do\n")
    if test_index != -1:
        return _join_with_caveats(text[:test_index], text[test_index:])

    final_end_index = text.rfind("\nend")
    if final_end_index == -1:
        raise FormulaUpdateError("could not find where to insert Homebrew caveats")
    return _join_with_caveats(text[:final_end_index], text[final_end_index:])


def _join_with_caveats(prefix: str, suffix: str) -> str:
    return f"{prefix.rstrip()}\n\n{CAVEATS_METHOD}{suffix}"


def update_formula(path: Path, tag: str, revision: str, *, check: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    update = prepare_formula_update(text, tag, revision)
    if check and update.changed:
        current_tag, current_revision = parse_formula_pin(text)
        problems: list[str] = []
        if update.pin_changed:
            problems.append(f"pins {current_tag}@{current_revision}; expected {tag}@{revision}")
        if update.caveats_changed:
            problems.append("Homebrew caveats are missing or stale")
        raise FormulaUpdateError(f"{path} {'; '.join(problems)}. Run scripts/update_homebrew_formula.py --formula <path> to refresh it.")
    if update.changed:
        path.write_text(update.text, encoding="utf-8")
    return update.changed


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
