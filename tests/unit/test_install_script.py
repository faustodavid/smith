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
