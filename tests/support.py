from __future__ import annotations

import json
import threading
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlsplit

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
        github_max_concurrent_requests=2,
        github_rate_limit_max_sleep_seconds=120,
        gitlab_group="gitlab-org",
        gitlab_api_url="https://gitlab.com/api/v4",
        gitlab_timeout_seconds=30,
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


@dataclass(frozen=True)
class StubRequest:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: str


@dataclass(frozen=True)
class StubResponse:
    status: int
    body: Any
    headers: dict[str, str] | None = None


RouteHandler = Callable[[StubRequest], StubResponse]


class GitHubApiStubServer:
    def __init__(
        self,
        *,
        routes: dict[tuple[str, str], RouteHandler],
    ) -> None:
        self._routes = {(method.upper(), path): handler for (method, path), handler in routes.items()}
        self._lock = threading.Lock()
        self.requests: list[StubRequest] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._build_handler())
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _build_handler(self) -> type[BaseHTTPRequestHandler]:
        parent = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parent._dispatch(self)

            def do_POST(self) -> None:  # noqa: N802
                parent._dispatch(self)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

        return _Handler

    def _dispatch(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlsplit(handler.path)
        content_length = int(handler.headers.get("Content-Length", "0") or "0")
        body = handler.rfile.read(content_length).decode("utf-8", errors="replace") if content_length > 0 else ""
        request = StubRequest(
            method=handler.command.upper(),
            path=parsed.path,
            query={key: values for key, values in parse_qs(parsed.query, keep_blank_values=True).items()},
            headers={key: value for key, value in handler.headers.items()},
            body=body,
        )
        with self._lock:
            self.requests.append(request)
        route = self._routes.get((request.method, request.path))
        if route is None:
            response = StubResponse(status=404, body={"message": f"No route for {request.method} {request.path}"})
        else:
            response = route(request)

        response_headers = dict(response.headers or {})
        body_obj = response.body
        if isinstance(body_obj, (dict, list)):
            encoded = json.dumps(body_obj).encode("utf-8")
            response_headers.setdefault("Content-Type", "application/json")
        elif isinstance(body_obj, bytes):
            encoded = body_obj
            response_headers.setdefault("Content-Type", "text/plain; charset=utf-8")
        else:
            encoded = str(body_obj).encode("utf-8")
            response_headers.setdefault("Content-Type", "text/plain; charset=utf-8")

        handler.send_response(response.status)
        for key, value in response_headers.items():
            handler.send_header(key, value)
        handler.send_header("Content-Length", str(len(encoded)))
        handler.end_headers()
        handler.wfile.write(encoded)

    def start(self) -> "GitHubApiStubServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def __enter__(self) -> "GitHubApiStubServer":
        return self.start()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.stop()
