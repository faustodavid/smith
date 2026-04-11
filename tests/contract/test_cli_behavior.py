from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from smith.cli.parser import build_parser
from smith.config import RemoteConfig, SmithConfig
from smith.errors import SmithApiError, SmithAuthError

cli_main = importlib.import_module("smith.cli.main")


class _FakeClient:
    def __init__(self, payload: Any | None = None, err: Exception | None = None) -> None:
        self._payload = payload
        self._err = err

    def execute_discover_projects(self, *, remote_or_provider: str) -> Any:
        assert remote_or_provider in {"azdo", "github", "gitlab", "all"}
        if self._err is not None:
            raise self._err
        return self._payload


def _parser_test_config() -> SmithConfig:
    return SmithConfig(
        remotes={
            "azdo": RemoteConfig(
                name="azdo",
                provider="azdo",
                org="acme",
                host="dev.azure.com",
                token_env="AZURE_DEVOPS_PAT",
                enabled=True,
                api_url="https://dev.azure.com",
            ),
            "github": RemoteConfig(
                name="github",
                provider="github",
                org="octo-org",
                host="github.com",
                token_env="GITHUB_TOKEN",
                enabled=True,
                api_url="https://api.github.com",
            ),
            "gitlab": RemoteConfig(
                name="gitlab",
                provider="gitlab",
                org="gitlab-org",
                host="gitlab.com",
                token_env="GITLAB_TOKEN",
                enabled=True,
                api_url="https://gitlab.com/api/v4",
            ),
        },
        defaults={},
    )


@pytest.fixture(autouse=True)
def _patch_build_parser(monkeypatch: Any) -> None:
    monkeypatch.setattr(cli_main, "build_parser", lambda: build_parser(smith_config=_parser_test_config()))


def test_success_exit_code_zero(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr(
        cli_main,
        "_client_from_args",
        lambda args: _FakeClient(payload=[{"name": "proj-a"}]),
    )

    code = cli_main.main(["orgs", "azdo"])
    captured = capsys.readouterr()

    assert code == 0
    assert captured.out.strip() == "proj-a"


def test_partial_exit_code_five(monkeypatch: Any, capsys: Any) -> None:
    payload = {
        "remotes": {
            "azdo": {
                "ok": True,
                "data": [{"name": "proj-a"}],
                "warnings": ["partial data"],
                "partial": False,
                "error": None,
            }
        },
        "summary": {
            "requested_remote": "azdo",
            "queried": ["azdo"],
            "succeeded": ["azdo"],
            "failed": [],
        },
    }
    monkeypatch.setattr(cli_main, "_client_from_args", lambda args: _FakeClient(payload=payload))

    code = cli_main.main(["orgs", "azdo"])
    _ = capsys.readouterr()

    assert code == 5


def test_auth_and_api_exit_codes(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr(
        cli_main,
        "_client_from_args",
        lambda args: _FakeClient(err=SmithAuthError("auth failed")),
    )
    code_auth = cli_main.main(["orgs", "azdo"])
    auth_out = capsys.readouterr()

    assert code_auth == 3
    assert "auth failed" in auth_out.err

    monkeypatch.setattr(
        cli_main,
        "_client_from_args",
        lambda args: _FakeClient(err=SmithApiError("api failed")),
    )
    code_api = cli_main.main(["orgs", "azdo"])
    api_out = capsys.readouterr()

    assert code_api == 4
    assert "api failed" in api_out.err


def test_code_search_without_config_file_reports_config_error(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr(
        cli_main,
        "_client_from_args",
        lambda args: (_ for _ in ()).throw(
            ValueError("Config file not found at /tmp/smith/config.yaml. Run `smith config init` to create it.")
        ),
    )
    code = cli_main.main(["code", "search", "grafana"])
    captured = capsys.readouterr()

    assert code == cli_main.EXIT_INVALID_ARGS
    assert "Config file not found" in captured.err
    assert "Missing GITHUB_ORG" not in captured.err


def test_cache_clean_succeeds_without_configured_remotes(monkeypatch: Any, capsys: Any, tmp_path: Any) -> None:
    github_cache = tmp_path / "github-grep"
    github_cache.mkdir()

    monkeypatch.setenv("SMITH_GITHUB_GREP_CACHE_DIR", str(github_cache))
    monkeypatch.setattr(
        cli_main,
        "_client_from_args",
        lambda args: (_ for _ in ()).throw(AssertionError("client should not be created")),
    )

    code = cli_main.main(["cache", "clean"])
    captured = capsys.readouterr()

    assert code == 0
    assert str(github_cache) in captured.out
    assert not github_cache.exists()


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
