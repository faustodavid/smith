from __future__ import annotations

from typing import Any

import pytest
from tests.support import make_runtime_config

from smith import client as client_module
from smith.client import SmithClient


class _FakeProvider:
    instances: list["_FakeProvider"] = []
    provider_name = "unknown"

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.__class__.instances.append(self)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def _inner(**kwargs: Any) -> dict[str, Any]:
            self.calls.append((name, kwargs))
            return {
                "provider": self.provider_name,
                "method": name,
                "kwargs": kwargs,
            }

        return _inner


class _FakeAzdoProvider(_FakeProvider):
    instances: list["_FakeAzdoProvider"] = []
    provider_name = "azdo"


class _FakeGitHubProvider(_FakeProvider):
    instances: list["_FakeGitHubProvider"] = []
    provider_name = "github"


class _FakeGitLabProvider(_FakeProvider):
    instances: list["_FakeGitLabProvider"] = []
    provider_name = "gitlab"


def _install_client_fakes(monkeypatch: Any, runtime: Any) -> dict[str, Any]:
    _FakeAzdoProvider.instances.clear()
    _FakeGitHubProvider.instances.clear()
    _FakeGitLabProvider.instances.clear()
    calls: dict[str, Any] = {}

    def _fake_run_fanout(
        *,
        providers: list[str],
        requested_provider: str,
        operations: dict[str, Any],
        provider_entry_success: Any,
        provider_entry_error: Any,
    ) -> dict[str, Any]:
        provider_payloads = {}
        for provider in providers:
            provider_payloads[provider] = provider_entry_success(operations[provider]())
        calls["run_fanout"] = {
            "providers": providers,
            "requested_provider": requested_provider,
        }
        return {
            "providers": provider_payloads,
            "summary": {
                "requested_provider": requested_provider,
                "queried": providers,
                "succeeded": providers,
                "failed": [],
            },
        }

    monkeypatch.setattr(client_module, "parse_runtime_config", lambda **kwargs: runtime)
    monkeypatch.setattr(client_module, "configure_http_session", lambda session, **kwargs: calls.setdefault("session", kwargs))
    monkeypatch.setattr(client_module, "AzdoProvider", _FakeAzdoProvider)
    monkeypatch.setattr(client_module, "GitHubProvider", _FakeGitHubProvider)
    monkeypatch.setattr(client_module, "GitLabProvider", _FakeGitLabProvider)
    monkeypatch.setattr(client_module, "run_fanout", _fake_run_fanout)
    return calls


def test_client_requires_at_least_one_configured_provider(monkeypatch: Any) -> None:
    runtime = make_runtime_config(azdo_org="", github_org="", gitlab_group="")
    monkeypatch.setattr(client_module, "parse_runtime_config", lambda **kwargs: runtime)

    with pytest.raises(ValueError, match="No providers configured"):
        SmithClient(session=object())


def test_client_initializes_runtime_and_configures_session(monkeypatch: Any) -> None:
    runtime = make_runtime_config(timeout_seconds=45, github_timeout_seconds=50, gitlab_timeout_seconds=55)
    session = object()
    calls = _install_client_fakes(monkeypatch, runtime)

    client = SmithClient(session=session)

    assert client._main_session is session
    assert client.azdo_org == "acme"
    assert client.timeout_seconds == 45
    assert client.github_timeout_seconds == 50
    assert client.gitlab_timeout_seconds == 55
    assert calls["session"] == {"pool_connections": 16, "pool_maxsize": 32}


def test_client_lazily_creates_and_caches_provider_instances(monkeypatch: Any) -> None:
    runtime = make_runtime_config(github_org="", gitlab_group="")
    _install_client_fakes(monkeypatch, runtime)
    client = SmithClient(session=object())

    azdo_first = client._get_azdo()
    azdo_second = client._get_azdo()

    assert azdo_first is azdo_second
    assert len(_FakeAzdoProvider.instances) == 1
    with pytest.raises(ValueError, match="GitHub is not configured"):
        client._get_github()
    with pytest.raises(ValueError, match="GitLab is not configured"):
        client._get_gitlab()


def test_provider_entry_helpers_extract_warning_and_partial_state() -> None:
    warnings, partial = SmithClient._provider_warnings_and_partial({"warnings": ["", "warn", 7], "partial": 1})
    success = SmithClient._provider_entry_success({"warnings": ["warn"], "partial": True})
    error = SmithClient._provider_entry_error("api_error", "boom")

    assert warnings == ["warn", "7"]
    assert partial is True
    assert success["warnings"] == ["warn"]
    assert success["partial"] is True
    assert error["error"] == {"code": "api_error", "message": "boom"}
    assert SmithClient._provider_warnings_and_partial(["not-a-dict"]) == ([], False)


def test_fanout_normalizes_provider_and_preserves_order(monkeypatch: Any) -> None:
    runtime = make_runtime_config()
    calls = _install_client_fakes(monkeypatch, runtime)
    client = SmithClient(session=object())

    result = client._fanout(
        provider="all",
        operations={
            "azdo": lambda: {"provider": "azdo"},
            "github": lambda: {"provider": "github"},
            "gitlab": lambda: {"provider": "gitlab"},
        },
    )

    assert calls["run_fanout"] == {"providers": ["github", "gitlab", "azdo"], "requested_provider": "all"}
    assert result["providers"]["github"]["data"]["provider"] == "github"
    assert result["providers"]["gitlab"]["data"]["provider"] == "gitlab"
    assert result["providers"]["azdo"]["data"]["provider"] == "azdo"


def test_execute_cache_clean_removes_requested_cache_dirs(monkeypatch: Any, tmp_path: Any) -> None:
    github_cache = tmp_path / "github-grep"
    gitlab_cache = tmp_path / "gitlab-grep"
    github_cache.mkdir()
    gitlab_cache.mkdir()

    monkeypatch.setenv("SMITH_GITHUB_GREP_CACHE_DIR", str(github_cache))
    monkeypatch.setenv("SMITH_GITLAB_GREP_CACHE_DIR", str(gitlab_cache))

    result = SmithClient.execute_cache_clean(provider="github")

    assert result == {
        "cleaned": [str(github_cache)],
        "missing": [],
    }
    assert not github_cache.exists()
    assert gitlab_cache.exists()


@pytest.mark.parametrize(
    ("method_name", "kwargs", "expected_provider", "expected_method", "expected_kwargs"),
    [
        ("execute_discover_projects", {"provider": "azdo"}, "azdo", "list_projects", {}),
        ("execute_discover_projects", {"provider": "gitlab"}, "gitlab", "list_projects", {}),
        (
            "execute_code_grep",
            {
                "provider": "github",
                "project": "proj-a",
                "repo": "repo-a",
                "pattern": "error",
                "path": "/src",
                "branch": "main",
                "glob": "*.py",
                "output_mode": "content",
                "case_insensitive": True,
                "context_lines": 2,
                "from_line": 10,
                "to_line": 20,
            },
            "github",
            "grep",
            {
                "repo": "repo-a",
                "pattern": "error",
                "path": "/src",
                "branch": "main",
                "glob": "*.py",
                "output_mode": "content",
                "case_insensitive": True,
                "context_lines": 2,
                "from_line": 10,
                "to_line": 20,
            },
        ),
        (
            "execute_pr_get",
            {"provider": "github", "project": "proj-a", "repo": "repo-a", "pull_request_id": 17},
            "github",
            "get_pull_request",
            {"repo": "repo-a", "pull_request_id": 17},
        ),
        (
            "execute_pr_get",
            {"provider": "gitlab", "project": "proj-a", "repo": "repo-a", "pull_request_id": 17},
            "gitlab",
            "get_pull_request",
            {"repo": "repo-a", "pull_request_id": 17},
        ),
        (
            "execute_pr_threads",
            {"provider": "azdo", "project": "proj-a", "repo": "repo-a", "pull_request_id": 17},
            "azdo",
            "get_pull_request_threads",
            {"project": "proj-a", "repo": "repo-a", "pull_request_id": 17},
        ),
        (
            "execute_pr_threads",
            {"provider": "gitlab", "project": "proj-a", "repo": "repo-a", "pull_request_id": 17},
            "gitlab",
            "get_pull_request_threads",
            {"repo": "repo-a", "pull_request_id": 17},
        ),
        (
            "execute_ci_logs",
            {"provider": "github", "project": "proj-a", "repo": None, "build_id": 19},
            "github",
            "get_build_log",
            {"repo": "proj-a", "build_id": 19},
        ),
        (
            "execute_ci_logs",
            {"provider": "gitlab", "project": "proj-a", "repo": None, "build_id": 19},
            "gitlab",
            "get_build_log",
            {"repo": "proj-a", "build_id": 19},
        ),
        (
            "execute_ci_grep",
            {
                "provider": "github",
                "project": "proj-a",
                "repo": None,
                "build_id": 19,
                "log_id": 5,
                "pattern": "error",
                "output_mode": "count",
                "case_insensitive": False,
                "context_lines": 0,
                "from_line": 1,
                "to_line": 2,
            },
            "github",
            "grep_build_log",
            {
                "repo": "proj-a",
                "build_id": 19,
                "log_id": 5,
                "pattern": "error",
                "output_mode": "count",
                "case_insensitive": False,
                "context_lines": 0,
                "from_line": 1,
                "to_line": 2,
            },
        ),
        (
            "execute_ci_grep",
            {
                "provider": "gitlab",
                "project": "proj-a",
                "repo": None,
                "build_id": 19,
                "log_id": 5,
                "pattern": "error",
                "output_mode": "count",
                "case_insensitive": False,
                "context_lines": 0,
                "from_line": 1,
                "to_line": 2,
            },
            "gitlab",
            "grep_build_log",
            {
                "repo": "proj-a",
                "build_id": 19,
                "log_id": 5,
                "pattern": "error",
                "output_mode": "count",
                "case_insensitive": False,
                "context_lines": 0,
                "from_line": 1,
                "to_line": 2,
            },
        ),
        (
            "execute_work_get",
            {"provider": "github", "project": "proj-a", "repo": None, "work_item_id": 21},
            "github",
            "get_ticket_by_id",
            {"repo": "proj-a", "work_item_id": 21},
        ),
        (
            "execute_work_get",
            {"provider": "gitlab", "project": "proj-a", "repo": None, "work_item_id": 21},
            "gitlab",
            "get_ticket_by_id",
            {"repo": "proj-a", "work_item_id": 21},
        ),
        (
            "execute_work_search",
            {
                "provider": "github",
                "query": "incident",
                "project": "proj-a",
                "repo": "repo-a",
                "area": None,
                "work_item_type": None,
                "state": "Open",
                "assigned_to": "alice",
                "skip": 0,
                "take": 10,
            },
            "github",
            "search_work_items",
            {
                "query": "incident",
                "project": "proj-a",
                "repo": "repo-a",
                "state": "Open",
                "assigned_to": "alice",
                "skip": 0,
                "take": 10,
                "include_closed": True,
            },
        ),
        (
            "execute_work_search",
            {
                "provider": "gitlab",
                "query": "incident",
                "project": "proj-a",
                "repo": "repo-a",
                "area": None,
                "work_item_type": None,
                "state": "Open",
                "assigned_to": "alice",
                "skip": 0,
                "take": 10,
            },
            "gitlab",
            "search_work_items",
            {
                "query": "incident",
                "project": "proj-a",
                "repo": "repo-a",
                "state": "Open",
                "assigned_to": "alice",
                "skip": 0,
                "take": 10,
                "include_closed": True,
            },
        ),
        (
            "execute_work_mine",
            {"provider": "azdo", "project": "proj-a", "repo": "repo-a", "include_closed": False, "skip": 1, "take": 5},
            "azdo",
            "get_my_work_items",
            {"project": "proj-a", "include_closed": False, "skip": 1, "take": 5},
        ),
        (
            "execute_work_mine",
            {"provider": "gitlab", "project": "proj-a", "repo": "repo-a", "include_closed": False, "skip": 1, "take": 5},
            "gitlab",
            "get_my_work_items",
            {"project": "proj-a", "repo": "repo-a", "include_closed": False, "skip": 1, "take": 5},
        ),
    ],
)
def test_execute_methods_dispatch_to_provider_operations(
    monkeypatch: Any,
    method_name: str,
    kwargs: dict[str, Any],
    expected_provider: str,
    expected_method: str,
    expected_kwargs: dict[str, Any],
) -> None:
    runtime = make_runtime_config()
    _install_client_fakes(monkeypatch, runtime)
    client = SmithClient(session=object())

    result = getattr(client, method_name)(**kwargs)

    provider_entry = result["providers"][expected_provider]["data"]
    assert provider_entry["method"] == expected_method
    assert provider_entry["kwargs"] == expected_kwargs


def test_execute_discover_repos_for_azdo_project_calls_list_repositories(monkeypatch: Any) -> None:
    runtime = make_runtime_config()
    _install_client_fakes(monkeypatch, runtime)
    client = SmithClient(session=object())

    result = client.execute_discover_repos(provider="azdo", project="proj-a")

    provider_entry = result["providers"]["azdo"]["data"]
    assert provider_entry == [
        {
            "provider": "azdo",
            "method": "list_repositories",
            "kwargs": {"project": "proj-a"},
            "projectName": "proj-a",
        }
    ]


def test_execute_discover_repos_for_azdo_without_project_fans_out_projects(monkeypatch: Any) -> None:
    runtime = make_runtime_config()
    _install_client_fakes(monkeypatch, runtime)
    client = SmithClient(session=object())

    azdo = client._get_azdo()
    azdo.list_projects = lambda: [{"name": "proj-a"}, {"name": "proj-b"}]  # type: ignore[method-assign]
    azdo.list_repositories = lambda project: [{"name": f"{project}-repo"}]  # type: ignore[method-assign]

    result = client.execute_discover_repos(provider="azdo", project=None)

    assert result["providers"]["azdo"]["data"] == [
        {"name": "proj-a-repo", "projectName": "proj-a"},
        {"name": "proj-b-repo", "projectName": "proj-b"},
    ]


def test_execute_code_search_runs_all_provider_operations(monkeypatch: Any) -> None:
    runtime = make_runtime_config()
    _install_client_fakes(monkeypatch, runtime)
    client = SmithClient(session=object())

    result = client.execute_code_search(
        provider="all",
        query="grafana",
        project="proj-a",
        repos=["repo-a"],
        skip=2,
        take=5,
    )

    assert result["summary"]["queried"] == ["github", "gitlab", "azdo"]
    assert result["providers"]["github"]["data"]["method"] == "search_code"
    assert result["providers"]["gitlab"]["data"]["method"] == "search_code"
    assert result["providers"]["azdo"]["data"]["kwargs"]["repos"] == ["repo-a"]


def test_execute_code_search_all_skips_unconfigured_providers(monkeypatch: Any) -> None:
    runtime = make_runtime_config(azdo_org="", github_org="", gitlab_group="platform")
    calls = _install_client_fakes(monkeypatch, runtime)
    client = SmithClient(session=object())

    result = client.execute_code_search(
        provider="all",
        query="grafana",
        project=None,
        repos=None,
        skip=0,
        take=5,
    )

    assert calls["run_fanout"] == {"providers": ["gitlab"], "requested_provider": "all"}
    assert result["summary"]["queried"] == ["gitlab"]
    assert set(result["providers"]) == {"gitlab"}
    assert _FakeAzdoProvider.instances == []
    assert _FakeGitHubProvider.instances == []
    assert len(_FakeGitLabProvider.instances) == 1


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_execute_pr_list_uses_projects_as_repo_fallback(monkeypatch: Any, provider: str) -> None:
    runtime = make_runtime_config()
    _install_client_fakes(monkeypatch, runtime)
    client = SmithClient(session=object())

    result = client.execute_pr_list(
        provider=provider,
        projects=["repo-from-project"],
        repos=None,
        statuses=["active"],
        creators=["alice"],
        date_from=None,
        date_to=None,
        skip=0,
        take=10,
        exclude_drafts=False,
        include_labels=False,
    )

    assert result["providers"][provider]["data"]["kwargs"]["repos"] == ["repo-from-project"]

def test_legacy_wrapper_methods_delegate_to_canonical_operations(monkeypatch: Any) -> None:
    runtime = make_runtime_config()
    _install_client_fakes(monkeypatch, runtime)
    client = SmithClient(session=object())

    discover_projects = client.execute_projects_list(provider="gitlab")
    ci_logs = client.execute_build_logs(provider="gitlab", project=None, repo="repo-a", build_id=1)
    work_get = client.execute_board_ticket(provider="gitlab", project=None, repo="repo-a", work_item_id=2)

    assert discover_projects["providers"]["gitlab"]["data"]["method"] == "list_projects"
    assert ci_logs["providers"]["gitlab"]["data"]["method"] == "get_build_log"
    assert work_get["providers"]["gitlab"]["data"]["method"] == "get_ticket_by_id"
