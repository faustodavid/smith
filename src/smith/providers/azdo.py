from __future__ import annotations

import base64
from typing import Any

import requests
from azure.identity import DefaultAzureCredential

from smith.auth import resolve_auth
from smith.config import RuntimeConfig
from smith.credentials import configured_token
from smith.errors import SmithAuthError
from smith.providers.azdo_builds import AzdoBuildMixin
from smith.providers.azdo_code import AzdoCodeMixin
from smith.providers.azdo_pull_requests import AzdoPullRequestMixin
from smith.providers.azdo_work_items import AzdoWorkItemMixin
from smith.providers.base import BaseProvider

ADO_SCOPE = "499b84ac-1321-427f-aa17-267ca6975798/.default"


class AzdoProvider(
    AzdoCodeMixin,
    AzdoPullRequestMixin,
    AzdoBuildMixin,
    AzdoWorkItemMixin,
    BaseProvider,
):
    def __init__(
        self,
        *,
        config: RuntimeConfig,
        credential: Any | None = None,
        session: requests.Session,
        azdo_org: str | None = None,
        token_env: str | None = None,
    ) -> None:
        super().__init__(config=config, session=session, token_env=token_env)
        self.org_name = azdo_org or config.azdo_org
        self.org_url = f"https://dev.azure.com/{self.org_name}" if self.org_name else config.azdo_org_url
        self._almsearch_base_url = f"https://almsearch.dev.azure.com/{self.org_name}" if self.org_name else config.azdo_search_url
        self.api_version = config.api_version
        self.max_output_chars = config.max_output_chars
        self._credential = credential or DefaultAzureCredential(exclude_interactive_browser_credential=True)
        self._access_token: str | None = None

    def _configured_pat(self) -> str | None:
        auth = resolve_auth("azdo", token_env=self._token_env)
        token = configured_token(auth)
        return token.token if token else None

    def _get_token(self, *, force_refresh: bool = False) -> str:
        pat = self._configured_pat()
        if pat:
            return pat
        if self._access_token and not force_refresh:
            return self._access_token

        try:
            token = self._credential.get_token(ADO_SCOPE)
        except Exception as exc:
            raise SmithAuthError(resolve_auth("azdo", token_env=self._token_env).missing_token_message) from exc

        self._access_token = token.token
        return self._access_token

    def _authorization_header(self, *, force_refresh: bool = False) -> str:
        pat = self._configured_pat()
        if pat:
            encoded = base64.b64encode(f":{pat}".encode("utf-8")).decode("ascii")
            return f"Basic {encoded}"
        return f"Bearer {self._get_token(force_refresh=force_refresh)}"

    def _auth_error_message(self) -> str:
        return resolve_auth("azdo", token_env=self._token_env).auth_rejected_message

    def _almsearch_url(self, suffix: str) -> str:
        return f"{self._almsearch_base_url}{suffix}"
