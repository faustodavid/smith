from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import requests
from tests.support import make_runtime_config

from smith.errors import SmithAuthError
from smith.providers.azdo import ADO_SCOPE, AzdoProvider


def _provider(config: Any | None = None, credential: Any | None = None) -> AzdoProvider:
    return AzdoProvider(config=config or make_runtime_config(), credential=credential, session=requests.Session())


def test_azdo_token_helpers_and_auth_failures() -> None:
    credential_calls: list[str] = []

    class _Credential:
        def get_token(self, scope: str) -> Any:
            credential_calls.append(scope)
            return SimpleNamespace(token="ado-token")

    provider = _provider(credential=_Credential())

    assert provider._get_token() == "ado-token"
    assert provider._get_token() == "ado-token"
    assert credential_calls == [ADO_SCOPE]
    assert provider._almsearch_url("/_apis/search") == "https://almsearch.dev.azure.com/acme/_apis/search"
    assert provider._auth_error_message() == "Authentication rejected with HTTP 401/403. Run `az login` and retry."

    class _FailingCredential:
        def get_token(self, scope: str) -> Any:
            raise RuntimeError("no az login")

    failing_provider = _provider(credential=_FailingCredential())
    with pytest.raises(SmithAuthError, match="Failed to acquire Azure DevOps token"):
        failing_provider._get_token()
