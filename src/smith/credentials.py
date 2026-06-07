from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum

from smith.auth import ResolvedAuth, normalize_token_env
from smith.errors import SmithAuthError
from smith.secure_store import get_stored_token


@dataclass(frozen=True)
class TokenLookup:
    token: str
    source: str


@dataclass(frozen=True)
class AuthProbe:
    ready: bool
    source: str | None = None


class AuthScanStatus(Enum):
    OK = "OK"
    MISSING = "MISSING"


@dataclass(frozen=True)
class AuthScan:
    status: AuthScanStatus
    message: str


def _is_likely_token(token: str) -> bool:
    return bool(token) and all(not char.isspace() for char in token)


def _command_label(command: tuple[str, ...]) -> str:
    return " ".join(command)


def _token_from_env_or_store(token_env: str) -> TokenLookup | None:
    token_env = normalize_token_env(token_env)
    env_token = os.getenv(token_env, "").strip()
    if env_token:
        return TokenLookup(token=env_token, source=f"{token_env} is set")

    stored_token = get_stored_token(token_env)
    if stored_token:
        return TokenLookup(token=stored_token, source=f"{token_env} is in secure store")

    return None


def configured_token(auth: ResolvedAuth) -> TokenLookup | None:
    if not auth.configured_token_env:
        return None
    return _token_from_env_or_store(auth.configured_token_env)


def runtime_env_token(auth: ResolvedAuth) -> TokenLookup | None:
    token = configured_token(auth)
    if token:
        return token
    if not auth.implicit_token_env:
        return None
    return _token_from_env_or_store(auth.implicit_token_env)


def _cli_token(auth: ResolvedAuth, *, timeout: float | None = None) -> TokenLookup | None:
    last_error: Exception | None = None
    saw_command_success = False

    for command in auth.cli_token_commands:
        try:
            result = subprocess.run(
                list(command),
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except Exception as exc:
            last_error = exc
            continue

        saw_command_success = True
        token = result.stdout.strip()
        if _is_likely_token(token):
            return TokenLookup(token=token, source=f"`{_command_label(command)}`")

    if saw_command_success:
        raise SmithAuthError(auth.empty_token_message) from last_error
    if auth.cli_token_commands:
        raise SmithAuthError(auth.missing_token_message) from last_error
    return None


def runtime_token(auth: ResolvedAuth, *, timeout: float | None = None) -> str:
    token = runtime_env_token(auth)
    if token:
        return token.token

    token = _cli_token(auth, timeout=timeout)
    if token:
        return token.token

    raise SmithAuthError(auth.missing_token_message)


def _status_command_ready(command: tuple[str, ...], *, timeout: float) -> bool:
    if not shutil.which(command[0]):
        return False
    try:
        result = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return False
    return result.returncode == 0


def probe_auth(auth: ResolvedAuth, *, timeout: float = 3) -> AuthProbe:
    token = runtime_env_token(auth)
    if token:
        return AuthProbe(ready=True, source=token.source)

    try:
        token = _cli_token(auth, timeout=timeout)
    except SmithAuthError:
        token = None
    if token:
        return AuthProbe(ready=True, source=f"{token.source} returned a token")

    if auth.cli_status_command and _status_command_ready(auth.cli_status_command, timeout=timeout):
        source = auth.cli_login_command or _command_label(auth.cli_status_command)
        return AuthProbe(ready=True, source=f"{source} is ready")

    return AuthProbe(ready=False)


def auth_readiness_scan(auth: ResolvedAuth) -> AuthScan:
    probe = probe_auth(auth)
    if probe.ready and probe.source:
        return AuthScan(AuthScanStatus.OK, f"{auth.label}: {probe.source}")
    return AuthScan(AuthScanStatus.MISSING, f"{auth.label}: auth can be added during setup")


def runtime_source_label(auth: ResolvedAuth) -> str:
    sources: list[str] = []
    token_env = auth.configured_token_env or auth.implicit_token_env
    if token_env:
        sources.append(f"{token_env} env/secure-store")
    if auth.cli_login_command:
        sources.append(auth.cli_login_command)
    return " or ".join(sources) or "runtime auth"


def runtime_auth_message(auth: ResolvedAuth) -> str:
    return f"Smith will use {runtime_source_label(auth)} at runtime."


def token_source_label(auth: ResolvedAuth, _configured_token_env: str | None) -> str:
    return runtime_source_label(auth)
