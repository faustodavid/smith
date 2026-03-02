from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeConfig:
    org_url: str
    api_version: str
    timeout_seconds: int
    max_output_chars: int
    github_org: str
    github_api_url: str
    github_api_version: str
    github_timeout_seconds: int
    http_pool_maxsize: int
    http_pool_connections: int
    http_retry_max_attempts: int
    http_retry_backoff_seconds: float


def parse_bool_env(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def parse_int_env(
    name: str,
    *,
    default: int,
    min_value: int,
    max_value: int,
) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, parsed))


def parse_runtime_config(
    *,
    org_url: str,
    api_version: str | None,
    timeout_seconds: int | None,
    max_output_chars: int | None,
    github_api_url_default: str,
    github_api_version_default: str,
) -> RuntimeConfig:
    resolved_api_version = api_version or os.getenv("AZURE_DEVOPS_API_VERSION") or "7.1"
    timeout = int(os.getenv("AZURE_DEVOPS_TIMEOUT_SECONDS", timeout_seconds or 30))

    retry_backoff_env = os.getenv("SMITH_HTTP_RETRY_BACKOFF_SECONDS")
    try:
        parsed_backoff = float((retry_backoff_env or "").strip() or "0.4")
    except ValueError:
        parsed_backoff = 0.4

    return RuntimeConfig(
        org_url=org_url.rstrip("/"),
        api_version=resolved_api_version,
        timeout_seconds=timeout,
        max_output_chars=int(os.getenv("THANOS_LOCAL_MAX_OUTPUT_CHARS", max_output_chars or 10240)),
        github_org=os.getenv("GITHUB_ORG", "").strip(),
        github_api_url=os.getenv("GITHUB_API_URL", github_api_url_default).rstrip("/"),
        github_api_version=os.getenv("GITHUB_API_VERSION", github_api_version_default),
        github_timeout_seconds=int(os.getenv("GITHUB_TIMEOUT_SECONDS", timeout)),
        http_pool_maxsize=parse_int_env(
            "SMITH_HTTP_POOL_MAXSIZE",
            default=32,
            min_value=1,
            max_value=256,
        ),
        http_pool_connections=parse_int_env(
            "SMITH_HTTP_POOL_CONNECTIONS",
            default=16,
            min_value=1,
            max_value=256,
        ),
        http_retry_max_attempts=parse_int_env(
            "SMITH_HTTP_RETRY_MAX_ATTEMPTS",
            default=2,
            min_value=1,
            max_value=6,
        ),
        http_retry_backoff_seconds=max(0.0, min(10.0, parsed_backoff)),
    )
