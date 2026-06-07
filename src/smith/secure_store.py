from __future__ import annotations

from dataclasses import dataclass

import keyring
from keyring.errors import KeyringError

from smith.auth import is_valid_token_env as _is_valid_token_env
from smith.auth import normalize_token_env

SERVICE_NAME = "smith"


@dataclass(frozen=True)
class SecureStoreResult:
    ok: bool
    token_env: str
    message: str


def is_valid_token_env(token_env: str) -> bool:
    return _is_valid_token_env(token_env)


def _normalize_token_env(token_env: str) -> str:
    return normalize_token_env(token_env)


def store_token(token_env: str, token: str) -> SecureStoreResult:
    normalized_env = _normalize_token_env(token_env)
    normalized_token = token.strip()
    if not normalized_token:
        return SecureStoreResult(
            ok=False,
            token_env=normalized_env,
            message="Token was empty; nothing was stored.",
        )

    try:
        keyring.set_password(SERVICE_NAME, normalized_env, normalized_token)
    except (KeyringError, RuntimeError, OSError) as exc:
        return SecureStoreResult(
            ok=False,
            token_env=normalized_env,
            message=f"Secure storage is unavailable on this machine: {exc}",
        )

    return SecureStoreResult(
        ok=True,
        token_env=normalized_env,
        message=f"Stored {normalized_env} in the OS secure credential store.",
    )


def get_stored_token(token_env: str) -> str | None:
    try:
        normalized_env = _normalize_token_env(token_env)
        token = keyring.get_password(SERVICE_NAME, normalized_env)
    except (ValueError, KeyringError, RuntimeError, OSError):
        return None
    return token.strip() if token else None
