from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


def _load_formula_module() -> Any:
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "scripts" / "update_homebrew_formula.py"
    spec = importlib.util.spec_from_file_location("update_homebrew_formula", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


FORMULA = """# frozen_string_literal: true

class Smith < Formula
  url "https://github.com/faustodavid/smith.git",
      tag:      "v0.1.0",
      revision: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
end
"""

FORMULA_WITH_CAVEATS = """# frozen_string_literal: true

class Smith < Formula
  url "https://github.com/faustodavid/smith.git",
      tag:      "v0.1.0",
      revision: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

  def caveats
    <<~EOS
      Smith is installed.

      Start the guided setup:
        smith config init

      This creates your config, links the Smith agent skill, and helps add
      GitHub, GitLab, Azure DevOps, and YouTrack remotes securely.
    EOS
  end
end
"""


def test_update_formula_text_replaces_only_release_pin() -> None:
    updater = _load_formula_module()

    updated = updater.update_formula_text(FORMULA, "v0.1.1", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")

    assert 'tag:      "v0.1.1"' in updated
    assert 'revision: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"' in updated
    assert "https://github.com/faustodavid/smith.git" in updated
    assert "Start the guided setup:" in updated
    assert "smith config init" in updated


def test_update_formula_check_fails_when_formula_is_stale(tmp_path: Path) -> None:
    updater = _load_formula_module()
    formula = tmp_path / "smith.rb"
    formula.write_text(FORMULA, encoding="utf-8")

    with pytest.raises(updater.FormulaUpdateError, match="pins v0.1.0@"):
        updater.update_formula(formula, "v0.1.1", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", check=True)

    assert 'tag:      "v0.1.0"' in formula.read_text(encoding="utf-8")


def test_update_formula_is_idempotent_when_current(tmp_path: Path) -> None:
    updater = _load_formula_module()
    formula = tmp_path / "smith.rb"
    formula.write_text(FORMULA_WITH_CAVEATS, encoding="utf-8")

    changed = updater.update_formula(formula, "v0.1.0", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", check=True)

    assert changed is False
    assert formula.read_text(encoding="utf-8") == FORMULA_WITH_CAVEATS


def test_update_formula_text_inserts_caveats_before_test_block() -> None:
    updater = _load_formula_module()
    formula = """# frozen_string_literal: true

class Smith < Formula
  url "https://github.com/faustodavid/smith.git",
      tag:      "v0.1.0",
      revision: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

  test do
    system "smith", "--help"
  end
end
"""

    updated = updater.update_formula_text(formula, "v0.1.1", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")

    assert updated.index("def caveats") < updated.index("test do")
    assert "smith config init" in updated


def test_update_formula_text_replaces_existing_caveats() -> None:
    updater = _load_formula_module()
    formula = FORMULA_WITH_CAVEATS.replace("smith config init", "old setup command")

    updated = updater.update_formula_text(formula, "v0.1.1", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")

    assert "old setup command" not in updated
    assert "smith config init" in updated


def test_load_project_version_reads_pyproject(tmp_path: Path) -> None:
    updater = _load_formula_module()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project]
name = "smith"
version = "1.2.3"
""".lstrip(),
        encoding="utf-8",
    )

    assert updater.load_project_version(pyproject) == "1.2.3"


def test_main_rejects_tag_that_does_not_match_project_version(tmp_path: Path) -> None:
    updater = _load_formula_module()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    formula = tmp_path / "smith.rb"
    formula.write_text(FORMULA, encoding="utf-8")

    with pytest.raises(updater.FormulaUpdateError, match="does not match project.version"):
        updater.main(
            [
                "--pyproject",
                str(pyproject),
                "--formula",
                str(formula),
                "--tag",
                "v1.2.4",
                "--revision",
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            ]
        )


def test_main_check_release_tag_exits_without_formula(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    updater = _load_formula_module()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")

    assert updater.main(["--pyproject", str(pyproject), "--tag", "v1.2.3", "--check-release-tag"]) == 0

    assert "v1.2.3 matches project.version 1.2.3" in capsys.readouterr().out


def test_main_check_release_tag_rejects_mismatch(tmp_path: Path) -> None:
    updater = _load_formula_module()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")

    with pytest.raises(updater.FormulaUpdateError, match="expected 'v1.2.3'"):
        updater.main(["--pyproject", str(pyproject), "--tag", "v1.2.4", "--check-release-tag"])


def test_main_requires_formula_unless_checking_release_tag(tmp_path: Path) -> None:
    updater = _load_formula_module()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        updater.main(["--pyproject", str(pyproject), "--tag", "v1.2.3", "--revision", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"])

    assert exc_info.value.code == 2


def test_update_formula_rejects_unsafe_tag() -> None:
    updater = _load_formula_module()

    with pytest.raises(updater.FormulaUpdateError, match="tag must start with v"):
        updater.update_formula_text(FORMULA, 'v1.2.3"; echo nope', "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")


SHA256 = "c" * 64

FORMULA_WITH_TARBALL = """# frozen_string_literal: true

class Smith < Formula
  url "https://github.com/faustodavid/smith/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "{sha256}"
end
""".format(sha256="d" * 64)

FORMULA_WITH_BOOTSTRAP = """# frozen_string_literal: true

class Smith < Formula
  url "https://github.com/faustodavid/smith.git",
      tag:      "v0.1.0",
      revision: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

  resource "setuptools" do
  end

  def install
    bootstrap_resources = %w[
      setuptools
      stale-entry
    ]
  end
end
"""


def test_update_formula_text_converts_git_pin_to_tarball() -> None:
    updater = _load_formula_module()

    updated = updater.update_formula_text(FORMULA, "v0.1.1", None, SHA256)

    assert 'url "https://github.com/faustodavid/smith/archive/refs/tags/v0.1.1.tar.gz"' in updated
    assert f'sha256 "{SHA256}"' in updated
    assert "revision:" not in updated


def test_update_formula_text_updates_existing_tarball_pin() -> None:
    updater = _load_formula_module()

    updated = updater.update_formula_text(FORMULA_WITH_TARBALL, "v0.1.1", None, SHA256)

    assert "refs/tags/v0.1.1.tar.gz" in updated
    assert f'sha256 "{SHA256}"' in updated
    assert "d" * 64 not in updated


def test_update_formula_text_requires_sha256_for_tarball_pin() -> None:
    updater = _load_formula_module()

    with pytest.raises(updater.FormulaUpdateError, match="pass --sha256"):
        updater.update_formula_text(FORMULA_WITH_TARBALL, "v0.1.1", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")


def test_update_formula_rejects_invalid_sha256() -> None:
    updater = _load_formula_module()

    with pytest.raises(updater.FormulaUpdateError, match="sha256 must be"):
        updater.update_formula_text(FORMULA, "v0.1.1", None, "nothex")


def test_parse_formula_pin_reads_both_forms() -> None:
    updater = _load_formula_module()

    git_pin = updater.parse_formula_pin(FORMULA)
    tarball_pin = updater.parse_formula_pin(FORMULA_WITH_TARBALL)

    assert (git_pin.tag, git_pin.revision, git_pin.sha256) == ("v0.1.0", "a" * 40, None)
    assert (tarball_pin.tag, tarball_pin.revision, tarball_pin.sha256) == ("v0.1.0", None, "d" * 64)


def test_ensure_bootstrap_resources_rewrites_canonical_list() -> None:
    updater = _load_formula_module()
    formula = FORMULA_WITH_BOOTSTRAP.replace(
        '  resource "setuptools" do\n  end\n',
        "".join(f'  resource "{name}" do\n  end\n' for name in updater.BOOTSTRAP_RESOURCES),
    )

    updated = updater.ensure_bootstrap_resources(formula)

    assert "stale-entry" not in updated
    for name in updater.BOOTSTRAP_RESOURCES:
        assert f"      {name}\n" in updated


def test_ensure_bootstrap_resources_fails_on_missing_stanza() -> None:
    updater = _load_formula_module()

    with pytest.raises(updater.FormulaUpdateError, match="no matching resource stanza"):
        updater.ensure_bootstrap_resources(FORMULA_WITH_BOOTSTRAP)


def test_main_print_extra_packages(capsys: pytest.CaptureFixture[str]) -> None:
    updater = _load_formula_module()

    assert updater.main(["--print-extra-packages"]) == 0

    out = capsys.readouterr().out.strip()
    assert out == ",".join(updater.BOOTSTRAP_RESOURCES)


def test_main_sync_bootstrap_resources_updates_formula(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    updater = _load_formula_module()
    formula = tmp_path / "smith.rb"
    formula.write_text(
        FORMULA_WITH_BOOTSTRAP.replace(
            '  resource "setuptools" do\n  end\n',
            "".join(f'  resource "{name}" do\n  end\n' for name in updater.BOOTSTRAP_RESOURCES),
        ),
        encoding="utf-8",
    )

    assert updater.main(["--formula", str(formula), "--sync-bootstrap-resources"]) == 0

    assert "bootstrap resources updated" in capsys.readouterr().out
    assert "stale-entry" not in formula.read_text(encoding="utf-8")
