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


def test_update_formula_rejects_unsafe_tag() -> None:
    updater = _load_formula_module()

    with pytest.raises(updater.FormulaUpdateError, match="tag must start with v"):
        updater.update_formula_text(FORMULA, 'v1.2.3"; echo nope', "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
