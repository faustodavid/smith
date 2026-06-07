from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from smith.auth import resolve_auth
from smith.credentials import configured_token, probe_auth, runtime_source_label, runtime_token, token_source_label
from smith.errors import SmithAuthError


def test_configured_token_prefers_env_over_secure_store(monkeypatch: Any) -> None:
    auth = resolve_auth("github", token_env="GITHUB_TOKEN", api_url="https://api.github.com")
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    monkeypatch.setattr("smith.credentials.get_stored_token", lambda token_env: "stored-token")

    token = configured_token(auth)

    assert token is not None
    assert token.token == "env-token"
    assert token.source == "GITHUB_TOKEN is set"


def test_configured_token_skips_implicit_runtime_env(monkeypatch: Any) -> None:
    auth = resolve_auth("github", api_url="https://api.github.com")
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")

    assert configured_token(auth) is None
    assert runtime_token(auth) == "env-token"


def test_runtime_token_uses_cli_token_command_after_configured_sources(monkeypatch: Any) -> None:
    auth = resolve_auth("gitlab", api_url="https://gitlab.example.test/api/v4")
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    monkeypatch.setattr("smith.credentials.get_stored_token", lambda token_env: None)
    calls: list[list[str]] = []

    def _fake_run(args: list[str], **kwargs: Any) -> Any:
        calls.append(args)
        return SimpleNamespace(stdout="cli-token\n")

    monkeypatch.setattr("smith.credentials.subprocess.run", _fake_run)

    assert runtime_token(auth) == "cli-token"
    assert calls == [["glab", "config", "get", "token", "--host", "gitlab.example.test"]]


def test_runtime_token_reports_empty_cli_token(monkeypatch: Any) -> None:
    auth = resolve_auth("github", api_url="https://api.github.com")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("smith.credentials.get_stored_token", lambda token_env: None)
    monkeypatch.setattr("smith.credentials.subprocess.run", lambda *args, **kwargs: SimpleNamespace(stdout="\n"))

    with pytest.raises(SmithAuthError, match="GitHub token is empty"):
        runtime_token(auth)


def test_probe_uses_azure_status_command_for_cli_readiness(monkeypatch: Any) -> None:
    auth = resolve_auth("azdo")
    calls: list[list[str]] = []

    def _fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="{}\n")

    monkeypatch.setattr("smith.credentials.shutil.which", lambda command: command)
    monkeypatch.setattr("smith.credentials.subprocess.run", _fake_run)

    probe = probe_auth(auth)

    assert probe.ready is True
    assert probe.source == "az login is ready"
    assert calls == [["az", "account", "show"]]


def test_token_source_label_uses_canonical_runtime_source_label() -> None:
    auth = resolve_auth("github", token_env="TEAM_GITHUB_TOKEN", api_url="https://api.github.com")

    assert token_source_label(auth, "TEAM_GITHUB_TOKEN") == runtime_source_label(auth)
    assert token_source_label(auth, "TEAM_GITHUB_TOKEN") == "TEAM_GITHUB_TOKEN env/secure-store or gh auth login"
