from __future__ import annotations

from typing import Any

import pytest
from keyring.errors import KeyringError

from smith import secure_store


def test_is_valid_token_env_rejects_token_like_values() -> None:
    assert secure_store.is_valid_token_env("GITHUB_TOKEN") is True
    assert secure_store.is_valid_token_env("TEAM_GITLAB_TOKEN_2") is True
    assert secure_store.is_valid_token_env("ghp_secret") is False
    assert secure_store.is_valid_token_env("github_pat_secret") is False
    assert secure_store.is_valid_token_env("TOKEN-WITH-DASH") is False


def test_store_and_read_token_with_keyring(monkeypatch: Any) -> None:
    stored: dict[tuple[str, str], str] = {}

    def _set_password(service: str, username: str, password: str) -> None:
        stored[(service, username)] = password

    def _get_password(service: str, username: str) -> str | None:
        return stored.get((service, username))

    monkeypatch.setattr(secure_store.keyring, "set_password", _set_password)
    monkeypatch.setattr(secure_store.keyring, "get_password", _get_password)

    result = secure_store.store_token("GITHUB_TOKEN", "  stored-token  ")

    assert result.ok is True
    assert secure_store.get_stored_token("GITHUB_TOKEN") == "stored-token"


def test_store_token_handles_unavailable_keyring(monkeypatch: Any) -> None:
    def _set_password(service: str, username: str, password: str) -> None:
        raise KeyringError("backend unavailable")

    monkeypatch.setattr(secure_store.keyring, "set_password", _set_password)

    result = secure_store.store_token("GITHUB_TOKEN", "stored-token")

    assert result.ok is False
    assert result.token_env == "GITHUB_TOKEN"
    assert "unavailable" in result.message


def test_store_token_rejects_invalid_token_env() -> None:
    with pytest.raises(ValueError, match="Token environment variable must look like"):
        secure_store.store_token("ghp_secretvalue", "stored-token")
