from __future__ import annotations

from typing import Any

import requests
from azure.identity import DefaultAzureCredential

from smith.config import RuntimeConfig
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
    ) -> None:
        super().__init__(config=config, session=session)
        self.org_name = config.azdo_org
        self.org_url = config.azdo_org_url
        self.api_version = config.api_version
        self.max_output_chars = config.max_output_chars
        self._credential = credential or DefaultAzureCredential(
            exclude_interactive_browser_credential=True
        )
        self._access_token: str | None = None

    def _get_token(self, *, force_refresh: bool = False) -> str:
        if self._access_token and not force_refresh:
            return self._access_token

        try:
            token = self._credential.get_token(ADO_SCOPE)
        except Exception as exc:
            raise SmithAuthError(
                "Failed to acquire Azure DevOps token using DefaultAzureCredential. "
                "Run `az login` and retry."
            ) from exc

        self._access_token = token.token
        return self._access_token

    def _auth_error_message(self) -> str:
        return "Authentication rejected with HTTP 401/403. Run `az login` and retry."

    def _almsearch_url(self, suffix: str) -> str:
        return f"{self._config.azdo_search_url}{suffix}"
