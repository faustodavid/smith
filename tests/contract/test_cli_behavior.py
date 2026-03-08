from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from smith.errors import SmithApiError, SmithAuthError

cli_main = importlib.import_module("smith.cli.main")


class _FakeClient:
    def __init__(self, payload: Any | None = None, err: Exception | None = None) -> None:
        self._payload = payload
        self._err = err

    def execute_discover_projects(self, *, provider: str) -> Any:
        assert provider in {"azdo", "github", "all"}
        if self._err is not None:
            raise self._err
        return self._payload


def test_success_exit_code_zero(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setenv("AZURE_DEVOPS_ORG", "test-org")
    monkeypatch.setattr(
        cli_main,
        "_client_from_args",
        lambda args: _FakeClient(payload=[{"name": "proj-a"}]),
    )

    code = cli_main.main(["organizations", "azdo"])
    captured = capsys.readouterr()

    assert code == 0
    assert captured.out.strip() == "proj-a"


def test_partial_exit_code_five(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setenv("AZURE_DEVOPS_ORG", "test-org")
    payload = {
        "providers": {
            "azdo": {
                "ok": True,
                "data": [{"name": "proj-a"}],
                "warnings": ["partial data"],
                "partial": False,
                "error": None,
            }
        },
        "summary": {
            "requested_provider": "azdo",
            "queried": ["azdo"],
            "succeeded": ["azdo"],
            "failed": [],
        },
    }
    monkeypatch.setattr(cli_main, "_client_from_args", lambda args: _FakeClient(payload=payload))

    code = cli_main.main(["organizations", "azdo"])
    _ = capsys.readouterr()

    assert code == 5


def test_auth_and_api_exit_codes(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setenv("AZURE_DEVOPS_ORG", "test-org")
    monkeypatch.setattr(
        cli_main,
        "_client_from_args",
        lambda args: _FakeClient(err=SmithAuthError("auth failed")),
    )
    code_auth = cli_main.main(["organizations", "azdo"])
    auth_out = capsys.readouterr()

    assert code_auth == 3
    assert "auth failed" in auth_out.err

    monkeypatch.setattr(
        cli_main,
        "_client_from_args",
        lambda args: _FakeClient(err=SmithApiError("api failed")),
    )
    code_api = cli_main.main(["organizations", "azdo"])
    api_out = capsys.readouterr()

    assert code_api == 4
    assert "api failed" in api_out.err


def test_script_entrypoint_smoke() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "scripts/smith_cli.py", "--help"],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "usage: smith" in result.stdout
