from __future__ import annotations

import threading
from typing import Any

import pytest
import requests

from smith.errors import SmithApiError, SmithAuthError
from smith.providers.base import BaseProvider, normalize_provider, normalize_single_provider, resolve_providers
from tests.support import FakeResponse, RecordingSession, make_runtime_config


class _TestProvider(BaseProvider):
    def __init__(self, *, config: Any, session: requests.Session | RecordingSession) -> None:
        super().__init__(config=config, session=session)
        self.token_calls: list[bool] = []
        self.status_calls: list[tuple[int, str]] = []

    def _get_token(self, *, force_refresh: bool = False) -> str:
        self.token_calls.append(force_refresh)
        return "refresh-token" if force_refresh else "token"

    def _auth_error_message(self) -> str:
        return "auth failed"

    def _default_accept_header(self) -> str:
        return "application/test+json"

    def _default_headers(self) -> dict[str, str]:
        return {"X-Test": "1"}

    def _build_url(self, path: str) -> str:
        return f"https://example.test{path}"

    def _handle_response_status(self, response: Any, resolved_url: str) -> None:
        self.status_calls.append((int(response.status_code), resolved_url))


def test_provider_name_helpers_normalize_and_validate() -> None:
    assert normalize_provider("GITHUB") == "github"
    assert resolve_providers("all") == ["github", "azdo"]
    assert normalize_single_provider("azdo", command="repos.list") == "azdo"

    with pytest.raises(ValueError, match="provider must be one of"):
        normalize_provider("gitlab")
    with pytest.raises(ValueError, match="repos.list does not support provider 'all'"):
        normalize_single_provider("all", command="repos.list")


def test_get_http_session_reuses_main_and_worker_sessions(monkeypatch: Any) -> None:
    configure_calls: list[dict[str, Any]] = []
    provider = _TestProvider(config=make_runtime_config(), session=requests.Session())
    monkeypatch.setattr("smith.providers.base.configure_http_session", lambda session, **kwargs: configure_calls.append(kwargs))

    assert provider._get_http_session() is provider._session

    worker_sessions: list[tuple[requests.Session, requests.Session]] = []

    def _worker() -> None:
        first = provider._get_http_session()
        second = provider._get_http_session()
        worker_sessions.append((first, second))

    thread = threading.Thread(target=_worker)
    thread.start()
    thread.join()

    assert worker_sessions[0][0] is worker_sessions[0][1]
    assert worker_sessions[0][0] is not provider._session
    assert configure_calls == [{"pool_connections": 16, "pool_maxsize": 32}]


def test_retry_sleep_seconds_prefers_retry_after_and_caps_it() -> None:
    provider = _TestProvider(config=make_runtime_config(http_retry_backoff_seconds=0.5), session=RecordingSession())

    assert provider._retry_sleep_seconds(response=FakeResponse(429, headers={"Retry-After": "60"}), retry_index=0) == 30.0
    assert provider._retry_sleep_seconds(response=FakeResponse(503, headers={}), retry_index=2) == 2.0


def test_request_retries_get_request_exceptions(monkeypatch: Any) -> None:
    session = RecordingSession([requests.RequestException("boom"), FakeResponse(200, text='{"ok": true}', json_data={"ok": True})])
    provider = _TestProvider(config=make_runtime_config(http_retry_max_attempts=2), session=session)
    sleeps: list[float] = []
    monkeypatch.setattr("smith.providers.base.time.sleep", lambda seconds: sleeps.append(seconds))

    result = provider._request_json("GET", "/items")

    assert result == {"ok": True}
    assert len(session.calls) == 2
    assert sleeps == [0.4]
    assert session.calls[0]["headers"]["Authorization"] == "Bearer token"


def test_request_refreshes_auth_token_after_401() -> None:
    session = RecordingSession([FakeResponse(401, text="nope"), FakeResponse(200, text='{"ok": true}', json_data={"ok": True})])
    provider = _TestProvider(config=make_runtime_config(), session=session)

    result = provider._request_json("GET", "/items")

    assert result == {"ok": True}
    assert provider.token_calls == [False, True]
    assert session.calls[1]["headers"]["Authorization"] == "Bearer refresh-token"


def test_request_retries_retryable_get_statuses(monkeypatch: Any) -> None:
    session = RecordingSession(
        [
            FakeResponse(503, text="retry", headers={"Retry-After": "12"}),
            FakeResponse(200, text='{"ok": true}', json_data={"ok": True}),
        ]
    )
    provider = _TestProvider(config=make_runtime_config(http_retry_max_attempts=2), session=session)
    sleeps: list[float] = []
    monkeypatch.setattr("smith.providers.base.time.sleep", lambda seconds: sleeps.append(seconds))

    result = provider._request_json("GET", "/items")

    assert result == {"ok": True}
    assert sleeps == [12.0]


def test_request_does_not_retry_non_get_exceptions() -> None:
    session = RecordingSession([requests.RequestException("boom")])
    provider = _TestProvider(config=make_runtime_config(http_retry_max_attempts=3), session=session)

    with pytest.raises(SmithApiError, match="Request error for https://example.test/items: boom"):
        provider._request_json("POST", "/items")

    assert len(session.calls) == 1


def test_request_raises_auth_error_when_retry_still_unauthorized() -> None:
    session = RecordingSession([FakeResponse(401, text="nope"), FakeResponse(401, text="still nope")])
    provider = _TestProvider(config=make_runtime_config(), session=session)

    with pytest.raises(SmithAuthError, match="auth failed"):
        provider._request_json("GET", "/items")


def test_request_handles_text_empty_body_and_invalid_json() -> None:
    provider = _TestProvider(config=make_runtime_config(), session=RecordingSession([FakeResponse(200, text="plain text")]))
    assert provider._request_text("GET", "/items") == "plain text"

    provider = _TestProvider(config=make_runtime_config(), session=RecordingSession([FakeResponse(204, text="")]))
    assert provider._request_json("GET", "/items") == {}

    provider = _TestProvider(config=make_runtime_config(), session=RecordingSession([FakeResponse(200, text="   ")]))
    assert provider._request_json("GET", "/items") == {}

    provider = _TestProvider(
        config=make_runtime_config(),
        session=RecordingSession([FakeResponse(200, text="{", json_error=ValueError("bad json"))]),
    )
    with pytest.raises(SmithApiError, match="received invalid JSON"):
        provider._request_json("GET", "/items")


def test_request_truncates_non_success_text_and_records_status_hook() -> None:
    session = RecordingSession([FakeResponse(500, text="x" * 600)])
    provider = _TestProvider(config=make_runtime_config(http_retry_max_attempts=1), session=session)

    with pytest.raises(SmithApiError) as exc_info:
        provider._request_json("GET", "/items")

    assert "HTTP 500 for https://example.test/items" in str(exc_info.value)
    assert len(str(exc_info.value).split(": ", 1)[1]) < 540
    assert provider.status_calls == [(500, "https://example.test/items")]


def test_request_json_requires_dictionary_payload() -> None:
    provider = _TestProvider(
        config=make_runtime_config(),
        session=RecordingSession([FakeResponse(200, text="[]", json_data=["not", "a", "dict"])]),
    )

    with pytest.raises(SmithApiError, match="Expected dictionary response"):
        provider._request_json("GET", "/items")
