from __future__ import annotations

import os

import requests

from smith.config import RuntimeConfig
from smith.errors import SmithAuthError
from smith.providers.base import BaseProvider
from smith.providers.youtrack_issues import YouTrackIssueMixin


class YouTrackProvider(
    YouTrackIssueMixin,
    BaseProvider,
):
    def __init__(
        self,
        *,
        config: RuntimeConfig,
        session: requests.Session,
        youtrack_api_url: str,
        token_env: str | None = None,
    ) -> None:
        super().__init__(config=config, session=session, token_env=token_env)
        self.youtrack_api_url = youtrack_api_url.rstrip("/")
        self.youtrack_base_url = self._web_url_from_api_url(self.youtrack_api_url)
        self.max_output_chars = config.max_output_chars
        self._youtrack_token: str | None = None

    @staticmethod
    def _web_url_from_api_url(api_url: str) -> str:
        normalized = api_url.rstrip("/")
        if normalized.endswith("/api"):
            return normalized[: -len("/api")]
        return normalized

    def _resolved_token_env_var(self) -> str:
        return self._token_env or "YOUTRACK_TOKEN"

    def _get_token(self, *, force_refresh: bool = False) -> str:
        if self._youtrack_token and not force_refresh:
            return self._youtrack_token

        token_env_var = self._resolved_token_env_var()
        env_token = os.getenv(token_env_var, "").strip()
        if not env_token:
            raise SmithAuthError(
                f"Failed to acquire YouTrack token. Set {token_env_var} and retry."
            )

        self._youtrack_token = env_token
        return self._youtrack_token

    def _auth_error_message(self) -> str:
        token_env_var = self._resolved_token_env_var()
        return (
            "YouTrack authentication rejected with HTTP 401/403. "
            f"Set {token_env_var} and retry."
        )

    def _build_url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.youtrack_api_url}{path}"
