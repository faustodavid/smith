from __future__ import annotations

import subprocess
from typing import Any

import pytest

from smith.config import parse_bool_env, parse_int_env, parse_runtime_config


@pytest.mark.parametrize(
    ("raw_value", "default", "expected"),
    [
        ("true", False, True),
        ("false", True, False),
        ("1", False, True),
        ("0", True, False),
        ("yes", False, True),
        ("no", True, False),
        ("", True, True),
        (None, False, False),
    ],
)
def test_parse_bool_env_handles_expected_values(
    monkeypatch: Any,
    raw_value: str | None,
    default: bool,
    expected: bool,
) -> None:
    env_name = "SMITH_TEST_BOOL"
    if raw_value is None:
        monkeypatch.delenv(env_name, raising=False)
    else:
        monkeypatch.setenv(env_name, raw_value)

    assert parse_bool_env(env_name, default=default) is expected


@pytest.mark.parametrize(
    ("raw_value", "default", "min_value", "max_value", "expected"),
    [
        ("42", 10, 1, 100, 42),
        ("not-an-int", 10, 1, 100, 10),
        ("0", 10, 1, 100, 1),
        ("999", 10, 1, 100, 100),
        (None, 10, 1, 100, 10),
    ],
)
def test_parse_int_env_handles_parsing_and_bounds(
    monkeypatch: Any,
    raw_value: str | None,
    default: int,
    min_value: int,
    max_value: int,
    expected: int,
) -> None:
    env_name = "SMITH_TEST_INT"
    if raw_value is None:
        monkeypatch.delenv(env_name, raising=False)
    else:
        monkeypatch.setenv(env_name, raw_value)

    assert (
        parse_int_env(
            env_name,
            default=default,
            min_value=min_value,
            max_value=max_value,
        )
        == expected
    )


def test_parse_runtime_config_uses_defaults_when_env_not_set(monkeypatch: Any) -> None:
    for env_name in (
        "AZURE_DEVOPS_ORG",
        "AZURE_DEVOPS_API_VERSION",
        "AZURE_DEVOPS_TIMEOUT_SECONDS",
        "THANOS_LOCAL_MAX_OUTPUT_CHARS",
        "GITHUB_ORG",
        "GITHUB_API_URL",
        "GITHUB_API_VERSION",
        "GITHUB_TIMEOUT_SECONDS",
        "GITHUB_MAX_CONCURRENT_REQUESTS",
        "GITHUB_RATE_LIMIT_MAX_SLEEP_SECONDS",
        "GITLAB_GROUP",
        "GITLAB_HOST",
        "GITLAB_API_URL",
        "GITLAB_TIMEOUT_SECONDS",
        "SMITH_HTTP_POOL_MAXSIZE",
        "SMITH_HTTP_POOL_CONNECTIONS",
        "SMITH_HTTP_RETRY_MAX_ATTEMPTS",
        "SMITH_HTTP_RETRY_BACKOFF_SECONDS",
    ):
        monkeypatch.delenv(env_name, raising=False)

    runtime = parse_runtime_config(
        azdo_org="example",
        api_version=None,
        timeout_seconds=None,
        max_output_chars=None,
        github_api_url_default="https://api.github.com/",
        github_api_version_default="2022-11-28",
        gitlab_api_url_default="https://gitlab.com/api/v4/",
    )

    assert runtime.azdo_org == "example"
    assert runtime.azdo_org_url == "https://dev.azure.com/example"
    assert runtime.api_version == "7.1"
    assert runtime.timeout_seconds == 30
    assert runtime.max_output_chars == 10240
    assert runtime.github_org == ""
    assert runtime.github_api_url == "https://api.github.com"
    assert runtime.github_api_version == "2022-11-28"
    assert runtime.github_timeout_seconds == 30
    assert runtime.github_max_concurrent_requests == 2
    assert runtime.github_rate_limit_max_sleep_seconds == 120
    assert runtime.gitlab_group == ""
    assert runtime.gitlab_api_url == "https://gitlab.com/api/v4"
    assert runtime.gitlab_timeout_seconds == 30
    assert runtime.http_pool_maxsize == 32
    assert runtime.http_pool_connections == 16
    assert runtime.http_retry_max_attempts == 2
    assert runtime.http_retry_backoff_seconds == pytest.approx(0.4)


def test_parse_runtime_config_applies_timeout_and_backoff_overrides(monkeypatch: Any) -> None:
    monkeypatch.setenv("AZURE_DEVOPS_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("GITHUB_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("GITHUB_MAX_CONCURRENT_REQUESTS", "4")
    monkeypatch.setenv("GITHUB_RATE_LIMIT_MAX_SLEEP_SECONDS", "180")
    monkeypatch.setenv("GITLAB_TIMEOUT_SECONDS", "75")
    monkeypatch.setenv("SMITH_HTTP_RETRY_BACKOFF_SECONDS", "1.75")

    runtime = parse_runtime_config(
        azdo_org="example",
        api_version=None,
        timeout_seconds=None,
        max_output_chars=None,
        github_api_url_default="https://api.github.com/",
        github_api_version_default="2022-11-28",
        gitlab_api_url_default="https://gitlab.com/api/v4/",
    )

    assert runtime.timeout_seconds == 45
    assert runtime.github_timeout_seconds == 60
    assert runtime.github_max_concurrent_requests == 4
    assert runtime.github_rate_limit_max_sleep_seconds == 180
    assert runtime.gitlab_timeout_seconds == 75
    assert runtime.http_retry_backoff_seconds == pytest.approx(1.75)


@pytest.mark.parametrize("azure_timeout_value", ["", "not-a-number"])
def test_parse_runtime_config_handles_invalid_or_empty_azure_timeout_env(
    monkeypatch: Any,
    azure_timeout_value: str,
) -> None:
    monkeypatch.setenv("AZURE_DEVOPS_TIMEOUT_SECONDS", azure_timeout_value)
    monkeypatch.delenv("GITHUB_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("GITLAB_TIMEOUT_SECONDS", raising=False)

    runtime = parse_runtime_config(
        azdo_org="example",
        api_version=None,
        timeout_seconds=11,
        max_output_chars=None,
        github_api_url_default="https://api.github.com/",
        github_api_version_default="2022-11-28",
        gitlab_api_url_default="https://gitlab.com/api/v4/",
    )

    assert runtime.timeout_seconds == 11
    assert runtime.github_timeout_seconds == 11
    assert runtime.gitlab_timeout_seconds == 11


def test_parse_runtime_config_falls_back_for_invalid_retry_backoff(monkeypatch: Any) -> None:
    monkeypatch.setenv("SMITH_HTTP_RETRY_BACKOFF_SECONDS", "not-a-float")

    runtime = parse_runtime_config(
        azdo_org="example",
        api_version=None,
        timeout_seconds=None,
        max_output_chars=None,
        github_api_url_default="https://api.github.com/",
        github_api_version_default="2022-11-28",
        gitlab_api_url_default="https://gitlab.com/api/v4/",
    )

    assert runtime.http_retry_backoff_seconds == pytest.approx(0.4)


def test_parse_runtime_config_github_org_override(monkeypatch: Any) -> None:
    monkeypatch.delenv("GITHUB_ORG", raising=False)

    runtime = parse_runtime_config(
        azdo_org="example",
        api_version=None,
        timeout_seconds=None,
        max_output_chars=None,
        github_org="override-gh-org",
        github_api_url_default="https://api.github.com/",
        github_api_version_default="2022-11-28",
        gitlab_api_url_default="https://gitlab.com/api/v4/",
    )

    assert runtime.github_org == "override-gh-org"
    assert runtime.github_configured is True


def test_parse_runtime_config_github_org_env_fallback(monkeypatch: Any) -> None:
    monkeypatch.setenv("GITHUB_ORG", "env-gh-org")

    runtime = parse_runtime_config(
        azdo_org="example",
        api_version=None,
        timeout_seconds=None,
        max_output_chars=None,
        github_api_url_default="https://api.github.com/",
        github_api_version_default="2022-11-28",
        gitlab_api_url_default="https://gitlab.com/api/v4/",
    )

    assert runtime.github_org == "env-gh-org"


def test_parse_runtime_config_github_org_override_wins_over_env(monkeypatch: Any) -> None:
    monkeypatch.setenv("GITHUB_ORG", "env-gh-org")

    runtime = parse_runtime_config(
        azdo_org="example",
        api_version=None,
        timeout_seconds=None,
        max_output_chars=None,
        github_org="cli-override",
        github_api_url_default="https://api.github.com/",
        github_api_version_default="2022-11-28",
        gitlab_api_url_default="https://gitlab.com/api/v4/",
    )

    assert runtime.github_org == "cli-override"


def test_parse_runtime_config_gitlab_group_override(monkeypatch: Any) -> None:
    monkeypatch.delenv("GITLAB_GROUP", raising=False)

    runtime = parse_runtime_config(
        azdo_org="example",
        api_version=None,
        timeout_seconds=None,
        max_output_chars=None,
        github_api_url_default="https://api.github.com/",
        github_api_version_default="2022-11-28",
        gitlab_group="platform",
        gitlab_api_url_default="https://gitlab.com/api/v4/",
    )

    assert runtime.gitlab_group == "platform"
    assert runtime.gitlab_configured is True


def test_parse_runtime_config_gitlab_group_env_fallback(monkeypatch: Any) -> None:
    monkeypatch.setenv("GITLAB_GROUP", "platform/subgroup/")

    runtime = parse_runtime_config(
        azdo_org="example",
        api_version=None,
        timeout_seconds=None,
        max_output_chars=None,
        github_api_url_default="https://api.github.com/",
        github_api_version_default="2022-11-28",
        gitlab_api_url_default="https://gitlab.com/api/v4/",
    )

    assert runtime.gitlab_group == "platform/subgroup"


def test_parse_runtime_config_gitlab_group_override_wins_over_env(monkeypatch: Any) -> None:
    monkeypatch.setenv("GITLAB_GROUP", "env-group")

    runtime = parse_runtime_config(
        azdo_org="example",
        api_version=None,
        timeout_seconds=None,
        max_output_chars=None,
        github_api_url_default="https://api.github.com/",
        github_api_version_default="2022-11-28",
        gitlab_group="cli-group",
        gitlab_api_url_default="https://gitlab.example.com/api/v4/",
    )

    assert runtime.gitlab_group == "cli-group"
    assert runtime.gitlab_api_url == "https://gitlab.example.com/api/v4"


def test_parse_runtime_config_gitlab_host_env_fallback(monkeypatch: Any) -> None:
    monkeypatch.setenv("GITLAB_HOST", "gitlab.example.test")
    monkeypatch.delenv("GITLAB_API_URL", raising=False)

    runtime = parse_runtime_config(
        azdo_org="example",
        api_version=None,
        timeout_seconds=None,
        max_output_chars=None,
        github_api_url_default="https://api.github.com/",
        github_api_version_default="2022-11-28",
        gitlab_api_url_default="https://gitlab.com/api/v4/",
    )

    assert runtime.gitlab_api_url == "https://gitlab.example.test/api/v4"


def test_parse_runtime_config_gitlab_glab_host_fallback(monkeypatch: Any) -> None:
    monkeypatch.setenv("GITLAB_GROUP", "example-group")
    monkeypatch.delenv("GITLAB_HOST", raising=False)
    monkeypatch.delenv("GITLAB_API_URL", raising=False)

    def _fake_run(args: list[str], **kwargs: Any) -> Any:
        if args == ["glab", "config", "get", "host"]:
            return subprocess.CompletedProcess(args, 0, stdout="gitlab.com\n")
        if args == ["glab", "auth", "status"]:
            raise subprocess.CalledProcessError(
                1,
                args,
                output=(
                    "gitlab.com\n"
                    "  ! No token found (checked config file, keyring, and environment variables).\n"
                    "gitlab.example.test\n"
                    "  ✓ Logged in to gitlab.example.test as fausto\n"
                    "  ✓ Token found: **************************\n"
                ),
            )
        raise AssertionError(f"unexpected glab command: {args}")

    monkeypatch.setattr("smith.config.subprocess.run", _fake_run)

    runtime = parse_runtime_config(
        azdo_org="example",
        api_version=None,
        timeout_seconds=None,
        max_output_chars=None,
        github_api_url_default="https://api.github.com/",
        github_api_version_default="2022-11-28",
        gitlab_api_url_default="https://gitlab.com/api/v4/",
    )

    assert runtime.gitlab_api_url == "https://gitlab.example.test/api/v4"


def test_parse_runtime_config_gitlab_api_url_override_wins_over_host(monkeypatch: Any) -> None:
    monkeypatch.setenv("GITLAB_HOST", "gitlab.example.test")
    monkeypatch.setenv("GITLAB_API_URL", "https://gitlab.example.com/api/v4/")

    runtime = parse_runtime_config(
        azdo_org="example",
        api_version=None,
        timeout_seconds=None,
        max_output_chars=None,
        github_api_url_default="https://api.github.com/",
        github_api_version_default="2022-11-28",
        gitlab_api_url_default="https://gitlab.com/api/v4/",
    )

    assert runtime.gitlab_api_url == "https://gitlab.example.com/api/v4"


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [("0", 1), ("3", 3), ("99", 16)],
)
def test_parse_runtime_config_bounds_github_max_concurrent_requests(
    monkeypatch: Any,
    raw_value: str,
    expected: int,
) -> None:
    monkeypatch.setenv("GITHUB_MAX_CONCURRENT_REQUESTS", raw_value)

    runtime = parse_runtime_config(
        azdo_org="example",
        api_version=None,
        timeout_seconds=None,
        max_output_chars=None,
        github_api_url_default="https://api.github.com/",
        github_api_version_default="2022-11-28",
        gitlab_api_url_default="https://gitlab.com/api/v4/",
    )

    assert runtime.github_max_concurrent_requests == expected


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [("0", 1), ("90", 90), ("999", 900)],
)
def test_parse_runtime_config_bounds_github_rate_limit_max_sleep_seconds(
    monkeypatch: Any,
    raw_value: str,
    expected: int,
) -> None:
    monkeypatch.setenv("GITHUB_RATE_LIMIT_MAX_SLEEP_SECONDS", raw_value)

    runtime = parse_runtime_config(
        azdo_org="example",
        api_version=None,
        timeout_seconds=None,
        max_output_chars=None,
        github_api_url_default="https://api.github.com/",
        github_api_version_default="2022-11-28",
        gitlab_api_url_default="https://gitlab.com/api/v4/",
    )

    assert runtime.github_rate_limit_max_sleep_seconds == expected
