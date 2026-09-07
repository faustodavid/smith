from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from smith.benchmark import smith_cli
from smith.benchmark.smith_cli import InProcessSmithCliRunner


class _FakeClient:
    init_calls = 0

    def __init__(self, **kwargs: Any) -> None:
        del kwargs
        type(self).init_calls += 1
        self.project_calls = 0
        self.search_calls = 0

    def execute_discover_projects(self, *, remote_or_provider: str) -> list[dict[str, Any]]:
        del remote_or_provider
        self.project_calls += 1
        return [{"name": "openai"}]

    def execute_code_search(
        self,
        *,
        remote_or_provider: str,
        query: str,
        project: str | None,
        repos: list[str] | None,
        skip: int,
        take: int,
        glob: str | None = None,
    ) -> dict[str, Any]:
        del remote_or_provider, query, project, repos, skip, take, glob
        self.search_calls += 1
        return {"matchesCount": 1, "results": ["openai/openai-python:/README.md"]}


def test_in_process_runner_reuses_single_client(monkeypatch: Any) -> None:
    _FakeClient.init_calls = 0
    monkeypatch.setattr("smith.benchmark.smith_cli.SmithClient", _FakeClient)

    runner = InProcessSmithCliRunner()
    first = runner.execute("github orgs")
    second = runner.execute("github orgs")

    assert first == "openai"
    assert second == "openai"
    assert _FakeClient.init_calls == 1


def test_in_process_runner_caches_successful_normalized_commands(monkeypatch: Any) -> None:
    _FakeClient.init_calls = 0
    monkeypatch.setattr("smith.benchmark.smith_cli.SmithClient", _FakeClient)

    runner = InProcessSmithCliRunner()
    first = runner.execute('smith code search "webhook"')
    second = runner.execute('code search "webhook"')

    assert first.startswith("matches: 1")
    assert second == first
    assert _FakeClient.init_calls == 1
    assert runner._client is not None
    assert runner._client.search_calls == 1


@pytest.mark.parametrize("stage", ["_build_run_env", "validate_smith_cli_command"])
@pytest.mark.parametrize("cached", [False, True])
def test_in_process_runner_locks_command_preparation(monkeypatch, tmp_path, stage, cached) -> None:
    monkeypatch.setattr(smith_cli, "SmithClient", _FakeClient)
    monkeypatch.setattr(smith_cli.tempfile, "gettempdir", lambda: str(tmp_path))
    runner = InProcessSmithCliRunner(env={"SMITH_CONFIG": ""})
    if cached:
        runner.execute("github orgs")

    target = runner if stage == "_build_run_env" else smith_cli
    original = getattr(target, stage)

    def can_acquire_lock() -> bool:
        acquired = runner._lock.acquire(blocking=False)
        if acquired:
            runner._lock.release()
        return acquired

    with ThreadPoolExecutor(max_workers=1) as executor:

        def checked_preparation(*args, **kwargs):
            assert not executor.submit(can_acquire_lock).result(timeout=5), f"{stage} ran outside the execution lock"
            return original(*args, **kwargs)

        monkeypatch.setattr(target, stage, checked_preparation)
        assert runner.execute("github orgs") == "openai"
