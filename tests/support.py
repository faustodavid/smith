from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable

from smith.config import RuntimeConfig


def make_runtime_config(**overrides: Any) -> RuntimeConfig:
    base = RuntimeConfig(
        azdo_org="acme",
        api_version="7.1",
        timeout_seconds=30,
        max_output_chars=10240,
        github_org="octo-org",
        github_api_url="https://api.github.com",
        github_api_version="2022-11-28",
        github_timeout_seconds=30,
        http_pool_maxsize=32,
        http_pool_connections=16,
        http_retry_max_attempts=2,
        http_retry_backoff_seconds=0.4,
    )
    return replace(base, **overrides)


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        *,
        text: str = "",
        json_data: Any = None,
        headers: dict[str, str] | None = None,
        json_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self._json_data = json_data
        self._json_error = json_error

    def json(self) -> Any:
        if self._json_error is not None:
            raise self._json_error
        return self._json_data


class RecordingSession:
    def __init__(self, responses: Iterable[Any] | None = None) -> None:
        self._responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    def queue(self, *responses: Any) -> None:
        self._responses.extend(responses)

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> Any:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": params,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        if not self._responses:
            raise AssertionError("No queued response for request")
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

