from __future__ import annotations

from typing import Any

from tests.support import GitHubApiStubServer, StubRequest, StubResponse

from smith.benchmark.constants import BENCHMARK_GITHUB_ORG
from smith.benchmark.smith_cli import execute_smith_cli_command


def _benchmark_env(*, base_url: str) -> dict[str, str]:
    return {
        "GITHUB_TOKEN": "test-token",
        "GITHUB_API_URL": base_url,
        "SMITH_HTTP_RETRY_MAX_ATTEMPTS": "2",
        "SMITH_HTTP_RETRY_BACKOFF_SECONDS": "0",
        "GITHUB_TIMEOUT_SECONDS": "5",
        "GITHUB_RATE_LIMIT_MAX_SLEEP_SECONDS": "10",
    }


def test_benchmark_smith_cli_injects_benchmark_org_in_code_search() -> None:
    def search_handler(request: StubRequest) -> StubResponse:
        return StubResponse(
            status=200,
            body={
                "total_count": 1,
                "items": [
                    {"repository": {"name": "example-repo"}, "path": "src/file.py"},
                ],
            },
        )

    with GitHubApiStubServer(routes={("GET", "/search/code"): search_handler}) as server:
        output = execute_smith_cli_command(
            'code search "needle" --take 1',
            env=_benchmark_env(base_url=server.base_url),
        )

    assert "matches: 1" in output
    assert "example-repo:/src/file.py" in output
    assert len(server.requests) == 1
    assert server.requests[0].path == "/search/code"
    assert server.requests[0].query["q"] == [f"needle org:{BENCHMARK_GITHUB_ORG}"]


def test_benchmark_smith_cli_retries_secondary_rate_limit_without_auth_failure() -> None:
    state: dict[str, Any] = {"search_calls": 0}

    def search_handler(_request: StubRequest) -> StubResponse:
        state["search_calls"] += 1
        if state["search_calls"] == 1:
            return StubResponse(
                status=403,
                body={"message": "You have exceeded a secondary rate limit."},
                headers={"Content-Type": "application/json"},
            )
        return StubResponse(
            status=200,
            body={"total_count": 0, "items": []},
        )

    with GitHubApiStubServer(routes={("GET", "/search/code"): search_handler}) as server:
        output = execute_smith_cli_command(
            'code search "needle" --take 1',
            env=_benchmark_env(base_url=server.base_url),
        )

    assert output == "matches: 0"
    assert state["search_calls"] == 2
    assert server.requests[0].path == "/search/code"
