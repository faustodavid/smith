from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock, patch

import pytest
import requests

import smith.http as http_module
from smith.http import (
    configure_http_session,
    configure_native_tls_trust,
    is_retryable_get_status,
    parse_rate_limit_reset_seconds,
    parse_retry_after_seconds,
)


def test_configure_http_session_mounts_adapter_for_real_requests_session() -> None:
    session = requests.Session()

    configure_http_session(session, pool_connections=7, pool_maxsize=13)

    https_adapter = session.adapters["https://"]
    http_adapter = session.adapters["http://"]
    assert https_adapter is http_adapter
    assert https_adapter._pool_connections == 7
    assert https_adapter._pool_maxsize == 13


def test_configure_http_session_ignores_objects_without_mount_method() -> None:
    class SessionWithoutMount:
        pass

    session = SessionWithoutMount()

    configure_http_session(session, pool_connections=1, pool_maxsize=1)

    assert isinstance(session, SessionWithoutMount)


def test_configure_http_session_swallows_mount_errors_from_mocked_session() -> None:
    session = Mock()
    session.mount.side_effect = TypeError("mock mount failure")

    configure_http_session(session, pool_connections=2, pool_maxsize=4)

    session.mount.assert_called_once()


def test_configure_native_tls_trust_injects_truststore_once(monkeypatch: Any) -> None:
    calls: list[str] = []

    class _FakeTrustStore:
        @staticmethod
        def inject_into_ssl() -> None:
            calls.append("inject")

    monkeypatch.setattr(http_module, "_NATIVE_TLS_TRUST_CONFIGURED", False)
    monkeypatch.setattr(http_module.importlib, "import_module", lambda name: _FakeTrustStore())

    configure_native_tls_trust()
    configure_native_tls_trust()

    assert calls == ["inject"]


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
def test_is_retryable_get_status_returns_true_for_retryable_codes(status_code: int) -> None:
    assert is_retryable_get_status(status_code) is True


@pytest.mark.parametrize("status_code", [200, 201, 400, 404])
def test_is_retryable_get_status_returns_false_for_non_retryable_codes(status_code: int) -> None:
    assert is_retryable_get_status(status_code) is False


def test_parse_retry_after_seconds_returns_none_for_none_response() -> None:
    assert parse_retry_after_seconds(None) is None


def test_parse_retry_after_seconds_returns_none_when_header_missing() -> None:
    response = Mock(headers={})

    assert parse_retry_after_seconds(response) is None


def test_parse_retry_after_seconds_parses_numeric_header() -> None:
    response = Mock(headers={"Retry-After": "12"})

    assert parse_retry_after_seconds(response) == 12.0


def test_parse_retry_after_seconds_returns_none_for_invalid_header_value() -> None:
    response = Mock(headers={"Retry-After": "not-valid"})

    assert parse_retry_after_seconds(response) is None


def test_parse_retry_after_seconds_parses_date_header() -> None:
    response = Mock(headers={"Retry-After": "Wed, 01 Jan 2025 00:00:10 GMT"})
    now = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)

    with patch("smith.http.datetime") as mocked_datetime:
        mocked_datetime.now.return_value = now
        seconds = parse_retry_after_seconds(response)

    assert seconds == 10.0


def test_parse_rate_limit_reset_seconds_returns_none_for_missing_header() -> None:
    response = Mock(headers={})

    assert parse_rate_limit_reset_seconds(response) is None


def test_parse_rate_limit_reset_seconds_returns_none_for_invalid_header() -> None:
    response = Mock(headers={"X-RateLimit-Reset": "not-a-number"})

    assert parse_rate_limit_reset_seconds(response) is None


def test_parse_rate_limit_reset_seconds_parses_future_epoch() -> None:
    response = Mock(headers={"X-RateLimit-Reset": "1735689610"})
    now = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)

    with patch("smith.http.datetime") as mocked_datetime:
        mocked_datetime.now.return_value = now
        seconds = parse_rate_limit_reset_seconds(response)

    assert seconds == 10.0


def test_parse_rate_limit_reset_seconds_clamps_past_epoch_to_zero() -> None:
    response = Mock(headers={"X-RateLimit-Reset": "1735689590"})
    now = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)

    with patch("smith.http.datetime") as mocked_datetime:
        mocked_datetime.now.return_value = now
        seconds = parse_rate_limit_reset_seconds(response)

    assert seconds == 0.0
