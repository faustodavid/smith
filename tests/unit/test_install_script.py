"""Unit tests for the top-level installer at scripts/install.py.

These tests focus on the supply-chain-safety-relevant behavior:
- `require_tool` exits cleanly with a helpful hint when a required tool is
  missing, without any network access or auto-install.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


def _load_install_module() -> Any:
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "scripts" / "install.py"
    spec = importlib.util.spec_from_file_location("smith_install_script", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_install_require_tool_fails_when_tool_missing(monkeypatch: Any, capsys: Any) -> None:
    install = _load_install_module()
    monkeypatch.setattr(install.shutil, "which", lambda name: None)

    with pytest.raises(SystemExit) as excinfo:
        install.require_tool(
            "rg",
            "Install ripgrep via your package manager.",
        )

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "rg is not installed" in captured.out
    assert "Install ripgrep" in captured.out


def test_install_require_tool_passes_when_tool_present(monkeypatch: Any) -> None:
    install = _load_install_module()
    monkeypatch.setattr(install.shutil, "which", lambda name: "/usr/bin/rg")

    install.require_tool("rg", "unused hint")


def test_sync_skill_preserves_existing_target_when_copy_fails(monkeypatch: Any, tmp_path: Path) -> None:
    install = _load_install_module()
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "SKILL.md").write_text("replacement", encoding="utf-8")
    existing = target / "SKILL.md"
    existing.write_text("existing", encoding="utf-8")

    def _failing_copytree(src: Path, dst: Path) -> None:
        raise RuntimeError("copy failed")

    monkeypatch.setattr(install.shutil, "copytree", _failing_copytree)

    with pytest.raises(RuntimeError, match="copy failed"):
        install.sync_skill(source, target)

    assert existing.read_text(encoding="utf-8") == "existing"


def test_sync_skill_via_cli_returns_false_when_smith_missing(monkeypatch: Any, tmp_path: Path) -> None:
    install = _load_install_module()
    monkeypatch.setattr(install.shutil, "which", lambda name: None)

    assert install.sync_skill_via_cli(tmp_path / "skills" / "smith") is False


def test_sync_skill_via_cli_runs_skill_sync_with_source_env(monkeypatch: Any, tmp_path: Path) -> None:
    install = _load_install_module()
    source = tmp_path / "skills" / "smith"
    calls: list[tuple[list[str], dict[str, str]]] = []

    monkeypatch.setattr(install.shutil, "which", lambda name: "/usr/local/bin/smith")

    class _Result:
        returncode = 0

    def _fake_run(cmd: list[str], **kwargs: Any) -> _Result:
        calls.append((cmd, kwargs["env"]))
        return _Result()

    monkeypatch.setattr(install.subprocess, "run", _fake_run)

    assert install.sync_skill_via_cli(source) is True
    assert calls == [(["/usr/local/bin/smith", "skill", "sync"], calls[0][1])]
    assert calls[0][1]["SMITH_SKILL_SOURCE_DIR"] == str(source)


def test_install_refuses_existing_non_git_directory(monkeypatch: Any, tmp_path: Path, capsys: Any) -> None:
    install = _load_install_module()
    repo_dir = tmp_path / "smith"
    repo_dir.mkdir()
    sentinel = repo_dir / "keep.txt"
    sentinel.write_text("do not delete", encoding="utf-8")
    monkeypatch.setattr(install, "REPO_DIR", repo_dir)
    monkeypatch.setattr(install, "SKILL_SOURCE", repo_dir / "skills" / "smith")
    monkeypatch.setattr(install, "TARGET_SKILL_DIR", tmp_path / "skill")
    monkeypatch.setattr(install, "require_tool", lambda *args, **kwargs: None)
    monkeypatch.setattr(install, "run", lambda *args, **kwargs: None)

    with pytest.raises(SystemExit) as excinfo:
        install.main()

    assert excinfo.value.code == 1
    assert sentinel.read_text(encoding="utf-8") == "do not delete"
    assert "refusing to replace non-git directory" in capsys.readouterr().err
