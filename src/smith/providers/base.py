from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Literal

import requests

from smith.config import RuntimeConfig
from smith.errors import SmithApiError, SmithAuthError
from smith.http import configure_http_session, is_retryable_get_status, parse_retry_after_seconds

ProviderName = Literal["azdo", "github", "all"]


def normalize_provider(provider: str | None) -> ProviderName:
    normalized = (provider or "azdo").strip().lower()
    if normalized not in {"azdo", "github", "all"}:
        raise ValueError("provider must be one of: azdo, github, all")
    return normalized  # type: ignore[return-value]


def resolve_providers(provider: str | None) -> list[str]:
    normalized = normalize_provider(provider)
    if normalized == "all":
        return ["github", "azdo"]
    return [normalized]


def normalize_single_provider(provider: str | None, *, command: str) -> str:
    normalized = normalize_provider(provider)
    if normalized == "all":
        raise ValueError(f"{command} does not support provider 'all'. Use azdo or github.")
    return normalized


class BaseProvider(ABC):
    def __init__(self, *, config: RuntimeConfig, session: requests.Session) -> None:
        self._config = config
        self._session = session
        self._http_thread_local = threading.local()

    @abstractmethod
    def _get_token(self, *, force_refresh: bool = False) -> str: ...

    @abstractmethod
    def _auth_error_message(self) -> str: ...

    def _default_accept_header(self) -> str:
        return "application/json"

    def _default_headers(self) -> dict[str, str]:
        return {}

    def _timeout(self) -> int:
        return self._config.timeout_seconds

    def _build_url(self, path: str) -> str:
        return path

    def _handle_response_status(self, response: Any, resolved_url: str) -> None:
        pass

    def _get_http_session(self, *, session: requests.Session | None = None) -> requests.Session:
        if session is not None:
            return session
        if threading.current_thread() is threading.main_thread():
            return self._session
        worker_session = getattr(self._http_thread_local, "session", None)
        if isinstance(worker_session, requests.Session):
            return worker_session
        worker_session = requests.Session()
        configure_http_session(
            worker_session,
            pool_connections=self._config.http_pool_connections,
            pool_maxsize=self._config.http_pool_maxsize,
        )
        self._http_thread_local.session = worker_session
        return worker_session

    def _retry_sleep_seconds(self, *, response: Any, retry_index: int) -> float:
        retry_after = parse_retry_after_seconds(response)
        if retry_after is not None:
            return min(30.0, retry_after)
        return self._config.http_retry_backoff_seconds * (2 ** max(0, retry_index))

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expect_json: bool = True,
        session: requests.Session | None = None,
    ) -> Any:
        method_upper = method.upper()
        max_attempts = self._config.http_retry_max_attempts
        is_retryable_get = method_upper == "GET" and max_attempts > 1
        http_session = self._get_http_session(session=session)
        resolved_url = self._build_url(url)
        timeout = self._timeout()

        request_headers = dict(headers or {})
        request_headers.setdefault("Accept", self._default_accept_header())
        for key, value in self._default_headers().items():
            request_headers.setdefault(key, value)

        response: Any = None
        for retry_index in range(max_attempts):
            attempt_headers = dict(request_headers)
            attempt_headers["Authorization"] = f"Bearer {self._get_token()}"
            try:
                response = http_session.request(
                    method,
                    resolved_url,
                    params=params,
                    json=json_body,
                    headers=attempt_headers,
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                if is_retryable_get and retry_index < max_attempts - 1:
                    time.sleep(self._retry_sleep_seconds(response=None, retry_index=retry_index))
                    continue
                raise SmithApiError(f"Request error for {resolved_url}: {exc}") from exc

            if response.status_code in (401, 403):
                retry_headers = dict(request_headers)
                retry_headers["Authorization"] = f"Bearer {self._get_token(force_refresh=True)}"
                response = http_session.request(
                    method,
                    resolved_url,
                    params=params,
                    json=json_body,
                    headers=retry_headers,
                    timeout=timeout,
                )

            if (
                is_retryable_get
                and is_retryable_get_status(int(response.status_code))
                and retry_index < max_attempts - 1
            ):
                time.sleep(self._retry_sleep_seconds(response=response, retry_index=retry_index))
                continue
            break

        if response is None:
            raise SmithApiError(f"No response received for {resolved_url}")

        if response.status_code in (401, 403):
            raise SmithAuthError(self._auth_error_message())

        self._handle_response_status(response, resolved_url)

        if not 200 <= response.status_code < 300:
            text = (response.text or "").strip()
            if len(text) > 500:
                text = text[:500] + "..."
            raise SmithApiError(
                f"HTTP {response.status_code} for {resolved_url}: {text}",
                status_code=response.status_code,
            )

        if not expect_json:
            return response.text

        if response.status_code == 204:
            return {}

        body = response.text or ""
        if not body.strip():
            return {}

        try:
            return response.json()
        except ValueError as exc:
            raise SmithApiError(
                f"Expected JSON response from {resolved_url} but received invalid JSON"
            ) from exc

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        session: requests.Session | None = None,
    ) -> dict[str, Any]:
        data = self._request(
            method,
            url,
            params=params,
            json_body=json_body,
            headers=headers,
            expect_json=True,
            session=session,
        )
        if isinstance(data, dict):
            return data
        raise SmithApiError(f"Expected dictionary response from {url}")

    def _request_text(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        session: requests.Session | None = None,
    ) -> str:
        data = self._request(
            method,
            url,
            params=params,
            headers=headers,
            expect_json=False,
            session=session,
        )
        return str(data)
