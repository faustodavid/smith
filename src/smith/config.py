from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse

AZDO_BASE_URL = "https://dev.azure.com"
AZDO_SEARCH_BASE_URL = "https://almsearch.dev.azure.com"
_GLAB_HOST_PATTERN = re.compile(r"^[A-Za-z0-9.-]+(?::\d+)?$")


@dataclass(frozen=True)
class RuntimeConfig:
    azdo_org: str
    api_version: str
    timeout_seconds: int
    max_output_chars: int
    github_org: str
    github_api_url: str
    github_api_version: str
    github_timeout_seconds: int
    github_max_concurrent_requests: int
    github_rate_limit_max_sleep_seconds: int
    gitlab_group: str
    gitlab_api_url: str
    gitlab_timeout_seconds: int
    http_pool_maxsize: int
    http_pool_connections: int
    http_retry_max_attempts: int
    http_retry_backoff_seconds: float

    @property
    def azdo_configured(self) -> bool:
        return bool(self.azdo_org)

    @property
    def github_configured(self) -> bool:
        return bool(self.github_org)

    @property
    def gitlab_configured(self) -> bool:
        return bool(self.gitlab_group)

    @property
    def azdo_org_url(self) -> str:
        return f"{AZDO_BASE_URL}/{self.azdo_org}" if self.azdo_org else ""

    @property
    def azdo_search_url(self) -> str:
        return f"{AZDO_SEARCH_BASE_URL}/{self.azdo_org}" if self.azdo_org else ""


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


def _normalize_gitlab_host(raw_host: str) -> str:
    parsed = urlparse(raw_host if "://" in raw_host else f"https://{raw_host}")
    return (parsed.netloc or parsed.path or "").strip().strip("/")


def _run_glab_command(args: list[str], *, allow_failure_output: bool = False) -> str:
    try:
        result = subprocess.run(args, check=True, capture_output=True, text=True)
        return (result.stdout or "").strip()
    except subprocess.CalledProcessError as exc:
        if not allow_failure_output:
            return ""
        parts = [part.strip() for part in ((exc.stdout or exc.output or ""), exc.stderr or "") if part and part.strip()]
        return "\n".join(parts).strip()
    except Exception:
        return ""


def _glab_auth_status_all_unsupported(status_output: str) -> bool:
    normalized = status_output.lower()
    return (
        ("unknown flag" in normalized and "--all" in normalized)
        or ("flag provided but not defined" in normalized and "--all" in normalized)
        or "accepts 0 arg(s), received 1" in normalized
    )


def _glab_auth_status_output() -> str:
    status_output = _run_glab_command(["glab", "auth", "status", "--all"], allow_failure_output=True)
    if status_output and not _glab_auth_status_all_unsupported(status_output):
        return status_output
    return _run_glab_command(["glab", "auth", "status"], allow_failure_output=True)


def _authenticated_glab_hosts() -> list[str]:
    status_output = _glab_auth_status_output()
    if not status_output:
        return []

    hosts: list[str] = []
    current_host = ""
    current_authenticated = False
    current_missing_token = False

    def flush() -> None:
        nonlocal current_host, current_authenticated, current_missing_token
        if current_host and current_authenticated and not current_missing_token:
            hosts.append(current_host)

    for line in [*status_output.splitlines(), ""]:
        stripped = line.strip()
        normalized_host = _normalize_gitlab_host(stripped.rstrip(":"))
        if normalized_host and _GLAB_HOST_PATTERN.fullmatch(normalized_host):
            flush()
            current_host = normalized_host
            current_authenticated = False
            current_missing_token = False
            continue
        if not current_host:
            continue
        if stripped.startswith("✓ Logged in to "):
            logged_in_host = _normalize_gitlab_host(stripped.removeprefix("✓ Logged in to ").split(" ", 1)[0])
            if logged_in_host == current_host:
                current_authenticated = True
        if "Token found:" in stripped:
            current_authenticated = True
        if "No token found" in stripped:
            current_missing_token = True

    flush()
    return hosts


def resolve_glab_gitlab_host() -> str:
    configured_host = _normalize_gitlab_host(_run_glab_command(["glab", "config", "get", "host"]))
    authenticated_hosts = _authenticated_glab_hosts()
    if configured_host and (not authenticated_hosts or configured_host in authenticated_hosts):
        return configured_host
    if len(authenticated_hosts) == 1:
        return authenticated_hosts[0]
    return configured_host


def _glab_api_protocol(host: str) -> str:
    normalized_host = _normalize_gitlab_host(host)
    if not normalized_host:
        return "https"
    protocol = _run_glab_command(
        ["glab", "config", "get", "api_protocol", "--host", normalized_host],
        allow_failure_output=True,
    ).strip().lower()
    return protocol if protocol in {"http", "https"} else "https"


def resolve_gitlab_api_url(*, default: str, enable_auto_discovery: bool = True) -> str:
    explicit_api_url = (os.getenv("GITLAB_API_URL", "") or "").strip().rstrip("/")
    if explicit_api_url:
        return explicit_api_url

    raw_host = (os.getenv("GITLAB_HOST", "") or "").strip().rstrip("/")
    normalized_default = default.rstrip("/")
    should_auto_discover = enable_auto_discovery and normalized_default == "https://gitlab.com/api/v4"
    auto_discovered_host = ""
    if not raw_host and should_auto_discover:
        auto_discovered_host = resolve_glab_gitlab_host()
        raw_host = auto_discovered_host
    if not raw_host:
        return normalized_default

    if auto_discovered_host:
        raw_host = f"{_glab_api_protocol(auto_discovered_host)}://{raw_host}"

    parsed = urlparse(raw_host if "://" in raw_host else f"https://{raw_host}")
    scheme = parsed.scheme or "https"
    netloc = (parsed.netloc or parsed.path or "").strip().strip("/")
    if not netloc:
        return normalized_default
    return f"{scheme}://{netloc}/api/v4"


def parse_runtime_config(
    *,
    azdo_org: str | None,
    api_version: str | None,
    timeout_seconds: int | None,
    max_output_chars: int | None,
    github_org: str | None = None,
    github_api_url_default: str,
    github_api_version_default: str,
    gitlab_group: str | None = None,
    gitlab_api_url_default: str,
) -> RuntimeConfig:
    resolved_azdo_org = (azdo_org or os.getenv("AZURE_DEVOPS_ORG", "") or "").strip()
    resolved_gitlab_group = (gitlab_group or os.getenv("GITLAB_GROUP", "") or "").strip().strip("/")

    resolved_api_version = api_version or os.getenv("AZURE_DEVOPS_API_VERSION") or "7.1"
    timeout = parse_int_env(
        "AZURE_DEVOPS_TIMEOUT_SECONDS",
        default=timeout_seconds or 30,
        min_value=1,
        max_value=300,
    )

    retry_backoff_env = os.getenv("SMITH_HTTP_RETRY_BACKOFF_SECONDS")
    try:
        parsed_backoff = float((retry_backoff_env or "").strip() or "0.4")
    except ValueError:
        parsed_backoff = 0.4

    return RuntimeConfig(
        azdo_org=resolved_azdo_org,
        api_version=resolved_api_version,
        timeout_seconds=timeout,
        max_output_chars=parse_int_env(
            "THANOS_LOCAL_MAX_OUTPUT_CHARS",
            default=max_output_chars or 10240,
            min_value=256,
            max_value=1_000_000,
        ),
        github_org=(github_org or os.getenv("GITHUB_ORG", "") or "").strip(),
        github_api_url=os.getenv("GITHUB_API_URL", github_api_url_default).rstrip("/"),
        github_api_version=os.getenv("GITHUB_API_VERSION", github_api_version_default),
        github_timeout_seconds=parse_int_env(
            "GITHUB_TIMEOUT_SECONDS",
            default=timeout,
            min_value=1,
            max_value=300,
        ),
        github_max_concurrent_requests=parse_int_env(
            "GITHUB_MAX_CONCURRENT_REQUESTS",
            default=2,
            min_value=1,
            max_value=16,
        ),
        github_rate_limit_max_sleep_seconds=parse_int_env(
            "GITHUB_RATE_LIMIT_MAX_SLEEP_SECONDS",
            default=120,
            min_value=1,
            max_value=900,
        ),
        gitlab_group=resolved_gitlab_group,
        gitlab_api_url=resolve_gitlab_api_url(
            default=gitlab_api_url_default,
            enable_auto_discovery=bool(resolved_gitlab_group),
        ),
        gitlab_timeout_seconds=parse_int_env(
            "GITLAB_TIMEOUT_SECONDS",
            default=timeout,
            min_value=1,
            max_value=300,
        ),
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
