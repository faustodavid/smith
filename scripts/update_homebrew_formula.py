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
TARBALL_PIN_RE = re.compile(
    r'url "https://github\.com/faustodavid/smith/archive/refs/tags/(?P<tag>[^"/]+)\.tar\.gz"\n'
    r'(?P<indent>[ \t]+)sha256 "(?P<sha256>[0-9a-f]{64})"'
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TAG_RE = re.compile(r"^v[0-9A-Za-z][0-9A-Za-z._-]*$")
# Build backends needed to install the vendored sdist resources without build
# isolation. Single source of truth for the formula's bootstrap_resources list
# and for `brew update-python-resources --extra-packages` in the tap workflow.
BOOTSTRAP_RESOURCES = (
    "setuptools",
    "flit-core",
    "packaging",
    "wheel",
    "cython",
    "pathspec",
    "pluggy",
    "trove-classifiers",
    "hatchling",
    "vcs-versioning",
    "setuptools-scm",
    "hatch-vcs",
)
BOOTSTRAP_RE = re.compile(r"(?P<indent>[ \t]+)bootstrap_resources = %w\[\n(?:[ \t]*[^\s\]]+\n)+[ \t]*\]")
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


def validate_tag_matches_version(tag: str, version: str) -> None:
    project_tag = expected_tag(version)
    if tag != project_tag:
        raise FormulaUpdateError(f"tag {tag!r} does not match project.version {version!r}; expected {project_tag!r}")


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


def validate_sha256(sha256: str) -> None:
    if not SHA256_RE.fullmatch(sha256):
        raise FormulaUpdateError(f"sha256 must be a 64-character lowercase hex digest, got {sha256!r}")


@dataclass(frozen=True)
class FormulaPin:
    tag: str
    revision: str | None
    sha256: str | None

    @property
    def checksum(self) -> str:
        return self.sha256 or self.revision or ""


def parse_formula_pin(text: str) -> FormulaPin:
    match = PIN_RE.search(text)
    if match:
        return FormulaPin(tag=match.group("tag"), revision=match.group("revision"), sha256=None)
    tarball = TARBALL_PIN_RE.search(text)
    if tarball:
        return FormulaPin(tag=tarball.group("tag"), revision=None, sha256=tarball.group("sha256"))
    raise FormulaUpdateError("could not find the Smith formula url pin")


def _tarball_pin_block(tag: str, sha256: str) -> str:
    return f'url "https://github.com/faustodavid/smith/archive/refs/tags/{tag}.tar.gz"\n  sha256 "{sha256}"'


def update_formula_pin_text(text: str, tag: str, revision: str | None, sha256: str | None = None) -> str:
    validate_tag(tag)

    if sha256 is not None:
        validate_sha256(sha256)
        replacement = _tarball_pin_block(tag, sha256)
        updated, count = TARBALL_PIN_RE.subn(replacement, text, count=1)
        if count != 1:
            updated, count = PIN_RE.subn(replacement, text, count=1)
        if count != 1:
            raise FormulaUpdateError("could not find the Smith formula url pin")
        return updated

    if revision is None:
        raise FormulaUpdateError("a revision is required to update the git pin; pass --sha256 to pin a release tarball instead")
    validate_revision(revision)

    def replace(match: re.Match[str]) -> str:
        return f'{match.group("prefix")}"{tag}"{match.group("middle")}"{revision}"'

    updated, count = PIN_RE.subn(replace, text, count=1)
    if count != 1:
        if TARBALL_PIN_RE.search(text):
            raise FormulaUpdateError("formula pins a release tarball; pass --sha256 to update it")
        raise FormulaUpdateError("could not find the Smith formula url tag/revision pin")
    return updated


def prepare_formula_update(text: str, tag: str, revision: str | None, sha256: str | None = None) -> FormulaUpdate:
    pin_updated = update_formula_pin_text(text, tag, revision, sha256)
    caveats_updated = ensure_formula_caveats(pin_updated)
    return FormulaUpdate(
        text=caveats_updated,
        pin_changed=pin_updated != text,
        caveats_changed=caveats_updated != pin_updated,
    )


def update_formula_text(text: str, tag: str, revision: str | None, sha256: str | None = None) -> str:
    return prepare_formula_update(text, tag, revision, sha256).text


def render_bootstrap_block(indent: str) -> str:
    entries = "\n".join(f"{indent}  {name}" for name in BOOTSTRAP_RESOURCES)
    return f"{indent}bootstrap_resources = %w[\n{entries}\n{indent}]"


def ensure_bootstrap_resources(text: str) -> str:
    match = BOOTSTRAP_RE.search(text)
    if not match:
        raise FormulaUpdateError("could not find the bootstrap_resources list in the formula")
    missing = [name for name in BOOTSTRAP_RESOURCES if f'resource "{name}" do' not in text]
    if missing:
        raise FormulaUpdateError(
            f"bootstrap resources have no matching resource stanza: {', '.join(missing)}. "
            "Run `brew update-python-resources` with the --extra-packages list from --print-extra-packages first."
        )
    return text[: match.start()] + render_bootstrap_block(match.group("indent")) + text[match.end() :]


def sync_bootstrap_resources(path: Path, *, check: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    updated = ensure_bootstrap_resources(text)
    changed = updated != text
    if check and changed:
        raise FormulaUpdateError(f"{path} bootstrap_resources list is stale. Rerun this script with --sync-bootstrap-resources.")
    if changed:
        path.write_text(updated, encoding="utf-8")
    return changed


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


def update_formula(path: Path, tag: str, revision: str | None, *, sha256: str | None = None, check: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    update = prepare_formula_update(text, tag, revision, sha256)
    if check and update.changed:
        current = parse_formula_pin(text)
        problems: list[str] = []
        if update.pin_changed:
            problems.append(f"pins {current.tag}@{current.checksum}; expected {tag}@{sha256 or revision}")
        if update.caveats_changed:
            problems.append("Homebrew caveats are missing or stale")
        raise FormulaUpdateError(f"{path} {'; '.join(problems)}. Run scripts/update_homebrew_formula.py --formula <path> to refresh it.")
    if update.changed:
        path.write_text(update.text, encoding="utf-8")
    return update.changed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Update a Homebrew formula to pin the current release tag and commit.")
    parser.add_argument("--formula", type=Path, help="Formula path to update, usually homebrew-tap/Formula/smith.rb.")
    parser.add_argument("--pyproject", type=Path, default=DEFAULT_PYPROJECT, help="pyproject.toml path used for the default tag.")
    parser.add_argument("--version", help="Project version to convert to a v-prefixed tag. Defaults to pyproject.toml.")
    parser.add_argument("--tag", help="Release tag to pin. Defaults to v{project.version}.")
    parser.add_argument("--revision", help="Release commit SHA. Defaults to resolving the selected tag with git.")
    parser.add_argument("--sha256", help="Release tarball sha256. When set, the formula pins the GitHub tag tarball, not a git revision.")
    parser.add_argument("--check", action="store_true", help="Fail if the formula is not already up to date.")
    parser.add_argument(
        "--check-release-tag",
        action="store_true",
        help="Validate that the selected release tag matches project.version, then exit without reading a formula.",
    )
    parser.add_argument(
        "--sync-bootstrap-resources",
        action="store_true",
        help="Rewrite the formula's bootstrap_resources list from the canonical list and exit. Run after `brew update-python-resources`.",
    )
    parser.add_argument(
        "--print-extra-packages",
        action="store_true",
        help="Print the canonical bootstrap package list for `brew update-python-resources --extra-packages`, then exit.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.print_extra_packages:
        print(",".join(BOOTSTRAP_RESOURCES))
        return 0

    if args.sync_bootstrap_resources:
        if args.formula is None:
            parser.error("--formula is required with --sync-bootstrap-resources")
        changed = sync_bootstrap_resources(args.formula, check=args.check)
        print(f"{args.formula}: bootstrap resources {'updated' if changed else 'already current'}")
        return 0

    version = args.version or load_project_version(args.pyproject)
    tag = args.tag or expected_tag(version)
    validate_tag(tag)
    validate_tag_matches_version(tag, version)

    if args.check_release_tag:
        print(f"{tag} matches project.version {version}")
        return 0

    if args.formula is None:
        parser.error("--formula is required unless --check-release-tag is set")

    revision = None if args.sha256 else (args.revision or resolve_tag_revision(tag))
    changed = update_formula(args.formula, tag, revision, sha256=args.sha256, check=args.check)
    status = "updated" if changed else "already current"
    print(f"{args.formula}: {status} at {tag}@{args.sha256 or revision}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FormulaUpdateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
