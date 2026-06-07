from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

_TOKEN_ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


@dataclass(frozen=True)
class ProviderAuthPolicy:
    provider: str
    label: str
    default_host: str
    default_token_env: str
    token_kind: str
    token_hint: str


@dataclass(frozen=True)
class ResolvedAuth:
    provider: str
    label: str
    configured_token_env: str | None
    implicit_token_env: str | None
    default_persist_token_env: str | None
    default_store_token_env: str | None
    cli_token_commands: tuple[tuple[str, ...], ...]
    cli_status_command: tuple[str, ...] | None
    cli_login_command: str | None
    missing_token_message: str
    empty_token_message: str
    auth_rejected_message: str


PROVIDER_AUTH_ORDER = ("github", "gitlab", "azdo", "youtrack")

_POLICIES = {
    "github": ProviderAuthPolicy(
        provider="github",
        label="GitHub",
        default_host="github.com",
        default_token_env="GITHUB_TOKEN",
        token_kind="token",
        token_hint="read-only GitHub token with Contents, Pull requests, Issues, and Actions read access",
    ),
    "gitlab": ProviderAuthPolicy(
        provider="gitlab",
        label="GitLab",
        default_host="gitlab.com",
        default_token_env="GITLAB_TOKEN",
        token_kind="token",
        token_hint="GitLab token with read_api and read_repository scopes",
    ),
    "azdo": ProviderAuthPolicy(
        provider="azdo",
        label="Azure DevOps",
        default_host="dev.azure.com",
        default_token_env="AZURE_DEVOPS_PAT",
        token_kind="PAT",
        token_hint="short-lived Azure DevOps PAT with read access to code, builds, pull requests, and work items",
    ),
    "youtrack": ProviderAuthPolicy(
        provider="youtrack",
        label="YouTrack",
        default_host="",
        default_token_env="YOUTRACK_TOKEN",
        token_kind="token",
        token_hint="YouTrack permanent token with the project/service scope Smith should read",
    ),
}


def is_valid_token_env(token_env: str) -> bool:
    return bool(_TOKEN_ENV_RE.fullmatch(token_env.strip()))


def normalize_token_env(token_env: str) -> str:
    normalized = token_env.strip()
    if not is_valid_token_env(normalized):
        raise ValueError("Token environment variable must look like GITHUB_TOKEN or TEAM_GITLAB_TOKEN.")
    return normalized


def normalize_optional_token_env(token_env: str | None) -> str | None:
    if token_env is None:
        return None
    stripped = token_env.strip()
    if not stripped:
        return None
    return normalize_token_env(stripped)


def provider_auth_policy(provider: str) -> ProviderAuthPolicy:
    normalized = provider.strip().lower()
    try:
        return _POLICIES[normalized]
    except KeyError as exc:
        raise ValueError(f"Unknown provider auth policy: {provider}") from exc


def github_web_host(api_url: str) -> str:
    parsed = urlparse(api_url if "://" in api_url else f"https://{api_url}")
    host = (parsed.netloc or parsed.path or "").strip().strip("/")
    if host == "api.github.com":
        return "github.com"
    if host.endswith("/api/v3"):
        return host[: -len("/api/v3")]
    return host


def gitlab_host(api_url: str) -> str:
    parsed = urlparse(api_url if "://" in api_url else f"https://{api_url}")
    return (parsed.netloc or parsed.path or "").strip().strip("/")


def _github_resolved_auth(policy: ProviderAuthPolicy, *, token_env: str | None, api_url: str) -> ResolvedAuth:
    host = github_web_host(api_url or "https://api.github.com")
    is_public = host == "github.com"
    implicit_token_env = None if token_env else policy.default_token_env if is_public else None
    login_command = "gh auth login" if is_public or not host else f"gh auth login --hostname {host}"
    token_command = ("gh", "auth", "token") if is_public or not host else ("gh", "auth", "token", "--hostname", host)

    if token_env:
        missing = f"Failed to acquire GitHub token. Set {token_env} or run `{login_command}`."
        empty = f"GitHub token is empty. Set {token_env} or run `{login_command}`."
        rejected = f"GitHub authentication rejected with HTTP 401/403. Set {token_env} or run `{login_command}` and retry."
    elif is_public:
        missing = "Failed to acquire GitHub token. Set GITHUB_TOKEN or run `gh auth login`."
        empty = "GitHub token is empty. Set GITHUB_TOKEN or run `gh auth login`."
        rejected = "GitHub authentication rejected with HTTP 401/403. Set GITHUB_TOKEN or run `gh auth login` and retry."
    else:
        missing = f"Failed to acquire GitHub token. Configure a host-specific token_env or run `{login_command}`."
        empty = f"GitHub token is empty. Configure a host-specific token_env or run `{login_command}`."
        rejected = (
            f"GitHub authentication rejected with HTTP 401/403. Configure a host-specific token_env or run `{login_command}` and retry."
        )

    return ResolvedAuth(
        provider=policy.provider,
        label=policy.label,
        configured_token_env=token_env,
        implicit_token_env=implicit_token_env,
        default_persist_token_env=token_env,
        default_store_token_env=token_env or implicit_token_env,
        cli_token_commands=(token_command,),
        cli_status_command=None,
        cli_login_command=login_command,
        missing_token_message=missing,
        empty_token_message=empty,
        auth_rejected_message=rejected,
    )


def _gitlab_resolved_auth(policy: ProviderAuthPolicy, *, token_env: str | None, api_url: str) -> ResolvedAuth:
    host = gitlab_host(api_url or "https://gitlab.com/api/v4")
    implicit_token_env = None if token_env else policy.default_token_env
    runtime_token_env = token_env or policy.default_token_env
    login_command = f"glab auth login --hostname {host}" if host and host != "gitlab.com" else "glab auth login"
    token_command = ("glab", "config", "get", "token", "--host", host) if host else ("glab", "config", "get", "token")

    if host:
        missing = f"Failed to acquire GitLab token for {host}. Set {runtime_token_env} or run `{login_command}`."
        empty = f"GitLab token is empty for {host}. Set {runtime_token_env} or run `{login_command}`."
    else:
        missing = f"Failed to acquire GitLab token. Set {runtime_token_env} or run `{login_command}`."
        empty = f"GitLab token is empty. Set {runtime_token_env} or run `{login_command}`."

    return ResolvedAuth(
        provider=policy.provider,
        label=policy.label,
        configured_token_env=token_env,
        implicit_token_env=implicit_token_env,
        default_persist_token_env=token_env,
        default_store_token_env=token_env or implicit_token_env,
        cli_token_commands=(token_command,),
        cli_status_command=None,
        cli_login_command=login_command,
        missing_token_message=missing,
        empty_token_message=empty,
        auth_rejected_message=(
            f"GitLab authentication rejected with HTTP 401/403. Set {runtime_token_env} or run `{login_command}` and retry."
        ),
    )


def _azdo_resolved_auth(policy: ProviderAuthPolicy, *, token_env: str | None) -> ResolvedAuth:
    rejected = (
        f"Authentication rejected with HTTP 401/403. Set {token_env} to a valid Azure DevOps PAT and retry."
        if token_env
        else "Authentication rejected with HTTP 401/403. Run `az login` and retry."
    )
    return ResolvedAuth(
        provider=policy.provider,
        label=policy.label,
        configured_token_env=token_env,
        implicit_token_env=None,
        default_persist_token_env=token_env or policy.default_token_env,
        default_store_token_env=token_env or policy.default_token_env,
        cli_token_commands=(),
        cli_status_command=("az", "account", "show"),
        cli_login_command="az login",
        missing_token_message="Failed to acquire Azure DevOps token using DefaultAzureCredential. Run `az login` and retry.",
        empty_token_message="Azure DevOps token is empty. Run `az login` and retry.",
        auth_rejected_message=rejected,
    )


def _youtrack_resolved_auth(policy: ProviderAuthPolicy, *, token_env: str | None) -> ResolvedAuth:
    implicit_token_env = None if token_env else policy.default_token_env
    runtime_token_env = token_env or policy.default_token_env
    return ResolvedAuth(
        provider=policy.provider,
        label=policy.label,
        configured_token_env=token_env,
        implicit_token_env=implicit_token_env,
        default_persist_token_env=token_env,
        default_store_token_env=token_env or implicit_token_env,
        cli_token_commands=(),
        cli_status_command=None,
        cli_login_command=None,
        missing_token_message=f"Failed to acquire YouTrack token. Set {runtime_token_env} and retry.",
        empty_token_message=f"YouTrack token is empty. Set {runtime_token_env} and retry.",
        auth_rejected_message=f"YouTrack authentication rejected with HTTP 401/403. Set {runtime_token_env} and retry.",
    )


def resolve_auth(provider: str, *, token_env: str | None = None, api_url: str = "") -> ResolvedAuth:
    policy = provider_auth_policy(provider)
    normalized_token_env = normalize_optional_token_env(token_env)
    if policy.provider == "github":
        return _github_resolved_auth(policy, token_env=normalized_token_env, api_url=api_url)
    if policy.provider == "gitlab":
        return _gitlab_resolved_auth(policy, token_env=normalized_token_env, api_url=api_url)
    if policy.provider == "azdo":
        return _azdo_resolved_auth(policy, token_env=normalized_token_env)
    if policy.provider == "youtrack":
        return _youtrack_resolved_auth(policy, token_env=normalized_token_env)
    raise ValueError(f"Unknown provider auth policy: {provider}")
