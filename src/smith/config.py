from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

AZDO_BASE_URL = "https://dev.azure.com"
AZDO_SEARCH_BASE_URL = "https://almsearch.dev.azure.com"
_GLAB_HOST_PATTERN = re.compile(r"^[A-Za-z0-9.-]+(?::\d+)?$")


@dataclass(frozen=True)
class RuntimeConfig:
    azdo_org: str
    api_version: str
    timeout_seconds: int
    max_output_chars: int
    grep_max_files: int
    github_org: str
    github_api_url: str
    github_api_version: str
    github_timeout_seconds: int
    github_max_concurrent_requests: int
    github_rate_limit_max_sleep_seconds: int
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


@dataclass(frozen=True)
class RemoteConfig:
    name: str
    provider: str
    org: str
    host: str
    token_env: str | None
    enabled: bool
    api_url: str


@dataclass(frozen=True)
class SmithConfig:
    remotes: dict[str, RemoteConfig]
    defaults: dict[str, Any]


def _default_config_path() -> Path:
    config_env = os.getenv("SMITH_CONFIG", "").strip()
    if config_env:
        return Path(config_env)
    return Path.home() / ".config" / "smith" / "config.yaml"


def _compute_api_url_for_remote(provider: str, host: str) -> str:
    if provider == "github":
        if not host or host == "github.com":
            return "https://api.github.com"
        return f"https://{host}/api/v3"
    if provider == "gitlab":
        if not host or host == "gitlab.com":
            return "https://gitlab.com/api/v4"
        parsed = urlparse(host if "://" in host else f"https://{host}")
        scheme = parsed.scheme or "https"
        netloc = (parsed.netloc or parsed.path or "").strip().strip("/")
        return f"{scheme}://{netloc}/api/v4"
    if provider == "azdo":
        return "https://dev.azure.com"
    return ""


_RESERVED_REMOTE_NAMES = {"all", "cache", "config", "help", "search"}


def _normalize_config_api_url(raw_api_url: Any) -> str:
    return str(raw_api_url or "").strip().rstrip("/")


def _load_remote_api_url(*, provider: str, remote: dict[str, Any], host: str) -> str:
    explicit_api_url = _normalize_config_api_url(remote.get("api_url", ""))
    if provider == "github" and explicit_api_url:
        return explicit_api_url
    return _compute_api_url_for_remote(provider, host)


def _should_persist_api_url(remote: RemoteConfig) -> bool:
    if remote.provider != "github":
        return False
    normalized_api_url = _normalize_config_api_url(remote.api_url)
    if not normalized_api_url:
        return False
    return normalized_api_url != _compute_api_url_for_remote(remote.provider, remote.host)


def _validate_remote_dict(name: str, remote: dict[str, Any]) -> None:
    if name.strip().lower() in _RESERVED_REMOTE_NAMES:
        reserved = ", ".join(sorted(_RESERVED_REMOTE_NAMES))
        raise ValueError(f"Remote '{name}': name is reserved. Choose a name outside: {reserved}")

    provider = remote.get("provider", "").strip().lower()
    if provider not in {"github", "gitlab", "azdo"}:
        raise ValueError(f"Remote '{name}': provider must be one of github, gitlab, azdo (got '{provider}')")

    if provider in {"github", "azdo"}:
        org = remote.get("org", "").strip()
        if not org:
            raise ValueError(f"Remote '{name}': {provider} remotes require 'org' field")


def load_config(*, config_path: Path | None = None) -> SmithConfig:
    path = config_path or _default_config_path()

    if not path.exists():
        raise ValueError(f"Config file not found at {path}. Run `smith config init` to create it.")

    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception as exc:
        raise ValueError(f"Failed to load config from {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"Config file {path} must contain a YAML mapping")

    remotes_dict: dict[str, RemoteConfig] = {}
    raw_remotes = raw.get("remotes", {})
    if not isinstance(raw_remotes, dict):
        raise ValueError("Config 'remotes' must be a mapping")

    for name, remote in raw_remotes.items():
        if not isinstance(remote, dict):
            raise ValueError(f"Remote '{name}' must be a mapping")
        _validate_remote_dict(name, remote)

        provider = remote["provider"].strip().lower()
        org = remote.get("org", "").strip()
        host = remote.get("host", "").strip()
        if not host:
            if provider == "github":
                host = "github.com"
            elif provider == "gitlab":
                host = "gitlab.com"
            elif provider == "azdo":
                host = "dev.azure.com"
        
        token_env = remote.get("token_env", "").strip() or None
        enabled = bool(remote.get("enabled", True))
        api_url = _load_remote_api_url(provider=provider, remote=remote, host=host)

        remotes_dict[name] = RemoteConfig(
            name=name,
            provider=provider,
            org=org,
            host=host,
            token_env=token_env,
            enabled=enabled,
            api_url=api_url,
        )

    defaults = raw.get("defaults", {})
    if not isinstance(defaults, dict):
        defaults = {}

    return SmithConfig(remotes=remotes_dict, defaults=defaults)


def save_config(config: SmithConfig, *, config_path: Path | None = None) -> None:
    path = config_path or _default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    remotes_dict: dict[str, dict[str, Any]] = {}
    for name, remote in config.remotes.items():
        remote_dict: dict[str, Any] = {
            "provider": remote.provider,
            "enabled": remote.enabled,
        }
        if remote.provider in {"github", "azdo"}:
            remote_dict["org"] = remote.org

        if remote.host:
            if remote.provider == "github" and remote.host != "github.com":
                remote_dict["host"] = remote.host
            elif remote.provider == "gitlab" and remote.host != "gitlab.com":
                remote_dict["host"] = remote.host
            elif remote.provider == "azdo" and remote.host != "dev.azure.com":
                remote_dict["host"] = remote.host

        if remote.token_env:
            remote_dict["token_env"] = remote.token_env

        if _should_persist_api_url(remote):
            remote_dict["api_url"] = _normalize_config_api_url(remote.api_url)

        remotes_dict[name] = remote_dict

    output = {"remotes": remotes_dict, "defaults": config.defaults}

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(output, f, default_flow_style=False, sort_keys=True)


def resolve_remote(config: SmithConfig, name: str) -> RemoteConfig | None:
    return config.remotes.get(name)


def parse_runtime_config(
    *,
    azdo_org: str | None,
    api_version: str | None,
    timeout_seconds: int | None,
    max_output_chars: int | None,
    github_api_url_default: str,
    github_api_version_default: str,
    gitlab_api_url_default: str,
) -> RuntimeConfig:
    resolved_azdo_org = (azdo_org or "").strip()

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
        grep_max_files=parse_int_env(
            "SMITH_GREP_MAX_FILES",
            default=5000,
            min_value=100,
            max_value=100_000,
        ),
        github_org="",
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
        gitlab_api_url=resolve_gitlab_api_url(
            default=gitlab_api_url_default,
            enable_auto_discovery=True,
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
