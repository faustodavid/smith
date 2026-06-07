from __future__ import annotations

import pytest

from smith.auth import is_valid_token_env, resolve_auth


def test_token_env_validation_rejects_token_like_values() -> None:
    assert is_valid_token_env("GITHUB_TOKEN") is True
    assert is_valid_token_env("TEAM_GITLAB_TOKEN_2") is True
    assert is_valid_token_env("ghp_secret") is False
    assert is_valid_token_env("github_pat_secret") is False
    assert is_valid_token_env("TOKEN-WITH-DASH") is False


def test_github_public_auth_uses_default_env_and_generic_gh_cli() -> None:
    auth = resolve_auth("github", api_url="https://api.github.com")

    assert auth.configured_token_env is None
    assert auth.implicit_token_env == "GITHUB_TOKEN"
    assert auth.default_persist_token_env is None
    assert auth.default_store_token_env == "GITHUB_TOKEN"
    assert auth.cli_token_commands == (("gh", "auth", "token"),)
    assert auth.cli_status_command is None
    assert auth.cli_login_command == "gh auth login"


def test_github_enterprise_auth_is_host_scoped() -> None:
    auth = resolve_auth("github", api_url="https://ghe.example.test/api/v3")

    assert auth.configured_token_env is None
    assert auth.implicit_token_env is None
    assert auth.default_persist_token_env is None
    assert auth.default_store_token_env is None
    assert auth.cli_token_commands == (("gh", "auth", "token", "--hostname", "ghe.example.test"),)
    assert auth.cli_status_command is None
    assert auth.cli_login_command == "gh auth login --hostname ghe.example.test"
    assert "GITHUB_TOKEN" not in auth.missing_token_message
    assert "GITHUB_TOKEN" not in auth.auth_rejected_message


def test_github_enterprise_explicit_token_env_is_allowed() -> None:
    auth = resolve_auth("github", token_env="GHE_TOKEN", api_url="https://ghe.example.test/api/v3")

    assert auth.configured_token_env == "GHE_TOKEN"
    assert auth.implicit_token_env is None
    assert auth.default_persist_token_env == "GHE_TOKEN"
    assert auth.default_store_token_env == "GHE_TOKEN"
    assert "Set GHE_TOKEN" in auth.missing_token_message


def test_resolve_auth_normalizes_explicit_token_env() -> None:
    auth = resolve_auth("github", token_env="  TEAM_GITHUB_TOKEN  ", api_url="https://api.github.com")

    assert auth.configured_token_env == "TEAM_GITHUB_TOKEN"
    assert auth.implicit_token_env is None
    assert auth.default_persist_token_env == "TEAM_GITHUB_TOKEN"
    assert auth.default_store_token_env == "TEAM_GITHUB_TOKEN"


def test_resolve_auth_rejects_invalid_explicit_token_env() -> None:
    with pytest.raises(ValueError, match="Token environment variable must look like"):
        resolve_auth("github", token_env="ghp_secretvalue", api_url="https://api.github.com")


def test_gitlab_auth_uses_default_env_and_host_scoped_glab_token_lookup() -> None:
    auth = resolve_auth("gitlab", api_url="https://gitlab.example.test/api/v4")

    assert auth.configured_token_env is None
    assert auth.implicit_token_env == "GITLAB_TOKEN"
    assert auth.default_persist_token_env is None
    assert auth.default_store_token_env == "GITLAB_TOKEN"
    assert auth.cli_token_commands == (("glab", "config", "get", "token", "--host", "gitlab.example.test"),)
    assert auth.cli_status_command is None
    assert auth.cli_login_command == "glab auth login --hostname gitlab.example.test"


def test_azdo_default_auth_does_not_enable_pat_env_unless_configured() -> None:
    auth = resolve_auth("azdo")
    configured_auth = resolve_auth("azdo", token_env="TEAM_AZDO_PAT")

    assert auth.configured_token_env is None
    assert auth.implicit_token_env is None
    assert auth.default_persist_token_env == "AZURE_DEVOPS_PAT"
    assert auth.default_store_token_env == "AZURE_DEVOPS_PAT"
    assert configured_auth.configured_token_env == "TEAM_AZDO_PAT"
    assert configured_auth.default_persist_token_env == "TEAM_AZDO_PAT"
    assert configured_auth.default_store_token_env == "TEAM_AZDO_PAT"


def test_youtrack_auth_uses_implicit_env_without_persisting_token_env() -> None:
    auth = resolve_auth("youtrack")

    assert auth.configured_token_env is None
    assert auth.implicit_token_env == "YOUTRACK_TOKEN"
    assert auth.default_persist_token_env is None
    assert auth.default_store_token_env == "YOUTRACK_TOKEN"
