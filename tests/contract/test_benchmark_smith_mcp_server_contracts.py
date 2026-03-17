from __future__ import annotations

import threading
import time
from typing import Any

from smith.benchmark import smith_mcp_server
from tests.support import GitHubApiStubServer, StubRequest, StubResponse


def _benchmark_env(*, base_url: str) -> dict[str, str]:
    return {
        "GITHUB_TOKEN": "test-token",
        "GITHUB_API_URL": base_url,
        "GITHUB_MAX_CONCURRENT_REQUESTS": "1",
        "SMITH_HTTP_RETRY_MAX_ATTEMPTS": "2",
        "SMITH_HTTP_RETRY_BACKOFF_SECONDS": "0",
        "GITHUB_TIMEOUT_SECONDS": "5",
        "GITHUB_RATE_LIMIT_MAX_SLEEP_SECONDS": "10",
    }


def test_benchmark_smith_mcp_server_shares_backpressure_across_concurrent_calls(
    monkeypatch: Any,
) -> None:
    state: dict[str, Any] = {
        "rate_limited_requests": 0,
        "successful_requests": 0,
        "first_request_started": False,
        "first_response_sent": False,
    }
    state_lock = threading.Lock()

    def search_handler(_request: StubRequest) -> StubResponse:
        is_first_request = False
        with state_lock:
            if not state["first_request_started"]:
                state["first_request_started"] = True
                is_first_request = True
            elif not state["first_response_sent"]:
                state["rate_limited_requests"] += 1
                return StubResponse(
                    status=429,
                    body={"message": "API rate limit exceeded"},
                    headers={"Retry-After": "1", "Content-Type": "application/json"},
                )

        if is_first_request:
            # Keep the first request in-flight long enough for a concurrent call to overlap.
            time.sleep(1.0)
            with state_lock:
                state["first_response_sent"] = True
                state["rate_limited_requests"] += 1
            return StubResponse(
                status=429,
                body={"message": "API rate limit exceeded"},
                headers={"Retry-After": "1", "Content-Type": "application/json"},
            )

        with state_lock:
            state["successful_requests"] += 1
            return StubResponse(
                status=200,
                body={
                    "total_count": 1,
                    "items": [{"repository": {"name": "repo-a"}, "path": "README.md"}],
                },
            )

    with GitHubApiStubServer(routes={("GET", "/search/code"): search_handler}) as server:
        env = _benchmark_env(base_url=server.base_url)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        barrier = threading.Barrier(3)
        outputs: list[str] = []
        errors: list[Exception] = []

        def run_call() -> None:
            barrier.wait()
            try:
                outputs.append(smith_mcp_server.smith_cli('code search "needle" --take 1'))
            except Exception as exc:  # pragma: no cover - assertion target captures this
                errors.append(exc)

        workers = [threading.Thread(target=run_call) for _ in range(2)]
        for thread in workers:
            thread.start()
        barrier.wait()
        for thread in workers:
            thread.join(timeout=10)

    assert not errors
    assert len(outputs) == 2
    assert all("matches: 1" in output for output in outputs)
    assert state["rate_limited_requests"] == 1
    assert state["successful_requests"] >= 1
    assert state["rate_limited_requests"] + state["successful_requests"] >= 2
