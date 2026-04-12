from __future__ import annotations

from typing import Any

import pytest
from tests.support import make_runtime_config

from smith import client as client_module
from smith.client import SmithClient
from smith.config import RemoteConfig, SmithConfig


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


def _make_smith_config(runtime: Any) -> SmithConfig:
    remotes: dict[str, RemoteConfig] = {}
    if runtime.github_org:
        remotes["github"] = RemoteConfig(
            name="github",
            provider="github",
            org=runtime.github_org,
            host="github.com",
            token_env="GITHUB_TOKEN",
            enabled=True,
            api_url=runtime.github_api_url,
        )
    if runtime.gitlab_api_url:
        remotes["gitlab"] = RemoteConfig(
            name="gitlab",
            provider="gitlab",
            org="gitlab-org",
            host="gitlab.com",
            token_env="GITLAB_TOKEN",
            enabled=True,
            api_url=runtime.gitlab_api_url,
        )
    if runtime.azdo_org:
        remotes["azdo"] = RemoteConfig(
            name="azdo",
            provider="azdo",
            org=runtime.azdo_org,
            host="dev.azure.com",
            token_env="AZURE_DEVOPS_PAT",
            enabled=True,
            api_url="https://dev.azure.com",
        )
    return SmithConfig(remotes=remotes, defaults={})


def _install_client_fakes(monkeypatch: Any, runtime: Any) -> dict[str, Any]:
    _FakeAzdoProvider.instances.clear()
    _FakeGitHubProvider.instances.clear()
    _FakeGitLabProvider.instances.clear()
    calls: dict[str, Any] = {}

    def _fake_run_fanout(
        *,
        remotes: list[str],
        requested_remote: str,
        operations: dict[str, Any],
        remote_entry_success: Any,
        remote_entry_error: Any,
    ) -> dict[str, Any]:
        del remote_entry_error
        remote_payloads = {}
        for remote in remotes:
            remote_payloads[remote] = remote_entry_success(operations[remote]())
        calls["run_fanout"] = {
            "remotes": remotes,
            "requested_remote": requested_remote,
        }
        return {
            "remotes": remote_payloads,
            "summary": {
                "requested_remote": requested_remote,
                "queried": remotes,
                "succeeded": remotes,
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
    runtime = make_runtime_config(azdo_org="", github_org="", gitlab_api_url="")
    monkeypatch.setattr(client_module, "parse_runtime_config", lambda **kwargs: runtime)

    with pytest.raises(ValueError, match="No remotes configured"):
        SmithClient(session=object(), smith_config=_make_smith_config(runtime))


def test_client_initializes_runtime_and_configures_session(monkeypatch: Any) -> None:
    runtime = make_runtime_config(timeout_seconds=45, github_timeout_seconds=50, gitlab_timeout_seconds=55)
    session = object()
    calls = _install_client_fakes(monkeypatch, runtime)

    client = SmithClient(session=session, smith_config=_make_smith_config(runtime))

    assert client._main_session is session
    assert client.timeout_seconds == 45
    assert client.max_output_chars == runtime.max_output_chars
    assert calls["session"] == {"pool_connections": 16, "pool_maxsize": 32}


def test_client_lazily_creates_and_caches_remote_provider_instances(monkeypatch: Any) -> None:
    runtime = make_runtime_config(github_org="", gitlab_api_url="")
    _install_client_fakes(monkeypatch, runtime)
    client = SmithClient(session=object(), smith_config=_make_smith_config(runtime))
    azdo_remote = client._config.remotes["azdo"]

    azdo_first = client._get_provider_for_remote(azdo_remote)
    azdo_second = client._get_provider_for_remote(azdo_remote)

    assert azdo_first is azdo_second
    assert len(_FakeAzdoProvider.instances) == 1
    assert "github" not in client._config.remotes
    assert "gitlab" not in client._config.remotes


def test_remote_entry_helpers_extract_warning_and_partial_state() -> None:
    warnings, partial = SmithClient._remote_warnings_and_partial({"warnings": ["", "warn", 7], "partial": 1})
    success = SmithClient._remote_entry_success({"warnings": ["warn"], "partial": True})
    error = SmithClient._remote_entry_error("api_error", "boom")

    assert warnings == ["warn", "7"]
    assert partial is True
    assert success["warnings"] == ["warn"]
    assert success["partial"] is True
    assert error["error"] == {"code": "api_error", "message": "boom"}
    assert SmithClient._remote_warnings_and_partial(["not-a-dict"]) == ([], False)


def test_fanout_normalizes_remote_and_preserves_order(monkeypatch: Any) -> None:
    runtime = make_runtime_config()
    calls = _install_client_fakes(monkeypatch, runtime)
    client = SmithClient(session=object(), smith_config=_make_smith_config(runtime))

    result = client._fanout(
        remote_or_provider="all",
        operations={
            "azdo": lambda remote: {"provider": remote.provider},
            "github": lambda remote: {"provider": remote.provider},
            "gitlab": lambda remote: {"provider": remote.provider},
        },
    )

    assert calls["run_fanout"] == {"remotes": ["github", "gitlab", "azdo"], "requested_remote": "all"}
    assert result["remotes"]["github"]["data"]["provider"] == "github"
    assert result["remotes"]["gitlab"]["data"]["provider"] == "gitlab"
    assert result["remotes"]["azdo"]["data"]["provider"] == "azdo"


def test_execute_cache_clean_removes_requested_cache_dirs(monkeypatch: Any, tmp_path: Any) -> None:
    github_cache = tmp_path / "github-grep"
    gitlab_cache = tmp_path / "gitlab-grep"
    github_cache.mkdir()
    gitlab_cache.mkdir()

    monkeypatch.setenv("SMITH_GITHUB_GREP_CACHE_DIR", str(github_cache))
    monkeypatch.setenv("SMITH_GITLAB_GREP_CACHE_DIR", str(gitlab_cache))

    runtime = make_runtime_config()
    result = SmithClient.execute_cache_clean(
        remote="github",
        smith_config=_make_smith_config(runtime),
    )

    assert result == {
        "cleaned": [str(github_cache)],
        "missing": [],
    }
    assert not github_cache.exists()
    assert gitlab_cache.exists()


@pytest.mark.parametrize(
    ("method_name", "kwargs", "expected_provider", "expected_method", "expected_kwargs"),
    [
        ("execute_discover_projects", {"remote_or_provider": "azdo"}, "azdo", "list_projects", {}),
        ("execute_discover_projects", {"remote_or_provider": "gitlab"}, "gitlab", "list_projects", {}),
        (
            "execute_list_groups",
            {"remote_or_provider": "gitlab", "grep": "^platform", "skip": 5, "take": 17},
            "gitlab",
            "discover_groups",
            {"query": client_module.DiscoveryQuery.create(grep="^platform", skip=5, take=17)},
        ),
        (
            "execute_code_grep",
            {
                "remote_or_provider": "github",
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
                "no_clone": True,
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
                "no_clone": True,
            },
        ),
        (
            "execute_pr_get",
            {"remote_or_provider": "github", "project": "proj-a", "repo": "repo-a", "pull_request_id": 17},
            "github",
            "get_pull_request",
            {"repo": "repo-a", "pull_request_id": 17},
        ),
        (
            "execute_pr_get",
            {"remote_or_provider": "gitlab", "project": "proj-a", "repo": "repo-a", "pull_request_id": 17},
            "gitlab",
            "get_pull_request",
            {"repo": "repo-a", "pull_request_id": 17},
        ),
        (
            "execute_pr_threads",
            {"remote_or_provider": "azdo", "project": "proj-a", "repo": "repo-a", "pull_request_id": 17},
            "azdo",
            "get_pull_request_threads",
            {"project": "proj-a", "repo": "repo-a", "pull_request_id": 17},
        ),
        (
            "execute_pr_threads",
            {"remote_or_provider": "gitlab", "project": "proj-a", "repo": "repo-a", "pull_request_id": 17},
            "gitlab",
            "get_pull_request_threads",
            {"repo": "repo-a", "pull_request_id": 17},
        ),
        (
            "execute_ci_logs",
            {"remote_or_provider": "github", "project": "proj-a", "repo": None, "build_id": 19},
            "github",
            "get_build_log",
            {"repo": "proj-a", "build_id": 19},
        ),
        (
            "execute_ci_logs",
            {"remote_or_provider": "gitlab", "project": "proj-a", "repo": None, "build_id": 19},
            "gitlab",
            "get_build_log",
            {"repo": "proj-a", "build_id": 19},
        ),
        (
            "execute_ci_grep",
            {
                "remote_or_provider": "github",
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
                "remote_or_provider": "gitlab",
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
            {"remote_or_provider": "github", "project": "proj-a", "repo": None, "work_item_id": 21},
            "github",
            "get_ticket_by_id",
            {"repo": "proj-a", "work_item_id": 21},
        ),
        (
            "execute_work_get",
            {"remote_or_provider": "gitlab", "project": "proj-a", "repo": None, "work_item_id": 21},
            "gitlab",
            "get_ticket_by_id",
            {"repo": "proj-a", "work_item_id": 21},
        ),
        (
            "execute_work_search",
            {
                "remote_or_provider": "github",
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
                "remote_or_provider": "gitlab",
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
            {"remote_or_provider": "azdo", "project": "proj-a", "repo": "repo-a", "include_closed": False, "skip": 1, "take": 5},
            "azdo",
            "get_my_work_items",
            {"project": "proj-a", "include_closed": False, "skip": 1, "take": 5},
        ),
        (
            "execute_work_mine",
            {"remote_or_provider": "gitlab", "project": "proj-a", "repo": "repo-a", "include_closed": False, "skip": 1, "take": 5},
            "gitlab",
            "get_my_work_items",
            {"project": "proj-a", "repo": "repo-a", "include_closed": False, "skip": 1, "take": 5},
        ),
    ],
)
def test_execute_methods_dispatch_to_remote_operations(
    monkeypatch: Any,
    method_name: str,
    kwargs: dict[str, Any],
    expected_provider: str,
    expected_method: str,
    expected_kwargs: dict[str, Any],
) -> None:
    runtime = make_runtime_config()
    _install_client_fakes(monkeypatch, runtime)
    client = SmithClient(session=object(), smith_config=_make_smith_config(runtime))

    result = getattr(client, method_name)(**kwargs)

    remote_entry = result["remotes"][expected_provider]["data"]
    assert remote_entry["method"] == expected_method
    assert remote_entry["kwargs"] == expected_kwargs


def test_execute_discover_repos_for_azdo_project_calls_list_repositories(monkeypatch: Any) -> None:
    runtime = make_runtime_config()
    _install_client_fakes(monkeypatch, runtime)
    client = SmithClient(session=object(), smith_config=_make_smith_config(runtime))

    result = client.execute_discover_repos(remote_or_provider="azdo", project="proj-a")

    remote_entry = result["remotes"]["azdo"]["data"]
    assert remote_entry == [
        {
            "provider": "azdo",
            "method": "list_repositories",
            "kwargs": {"project": "proj-a"},
            "projectName": "proj-a",
        }
    ]


def test_execute_discover_repos_for_gitlab_builds_discovery_query(monkeypatch: Any) -> None:
    runtime = make_runtime_config()
    _install_client_fakes(monkeypatch, runtime)
    client = SmithClient(session=object(), smith_config=_make_smith_config(runtime))

    result = client.execute_discover_repos(
        remote_or_provider="gitlab",
        project=None,
        group="engineering-tools",
        grep="^engineering-tools/(api|web)$",
        skip=10,
        take=25,
    )

    remote_entry = result["remotes"]["gitlab"]["data"]
    assert remote_entry["method"] == "discover_repositories"
    assert remote_entry["kwargs"] == {
        "group": "engineering-tools",
        "query": client_module.DiscoveryQuery.create(
            grep="^engineering-tools/(api|web)$",
            skip=10,
            take=25,
        ),
    }


def test_execute_discover_repos_for_azdo_without_project_fans_out_projects(monkeypatch: Any) -> None:
    runtime = make_runtime_config()
    _install_client_fakes(monkeypatch, runtime)
    client = SmithClient(session=object(), smith_config=_make_smith_config(runtime))

    azdo = client._get_provider_for_remote(client._config.remotes["azdo"])
    azdo.list_projects = lambda: [{"name": "proj-a"}, {"name": "proj-b"}]  # type: ignore[method-assign]
    azdo.list_repositories = lambda project: [{"name": f"{project}-repo"}]  # type: ignore[method-assign]

    result = client.execute_discover_repos(remote_or_provider="azdo", project=None)

    assert result["remotes"]["azdo"]["data"] == [
        {"name": "proj-a-repo", "projectName": "proj-a"},
        {"name": "proj-b-repo", "projectName": "proj-b"},
    ]


def test_execute_code_search_runs_all_remote_operations(monkeypatch: Any) -> None:
    runtime = make_runtime_config()
    _install_client_fakes(monkeypatch, runtime)
    client = SmithClient(session=object(), smith_config=_make_smith_config(runtime))

    result = client.execute_code_search(
        remote_or_provider="all",
        query="grafana",
        project="proj-a",
        repos=["repo-a"],
        skip=2,
        take=5,
    )

    assert result["summary"]["queried"] == ["github", "gitlab", "azdo"]
    assert result["remotes"]["github"]["data"]["method"] == "search_code"
    assert result["remotes"]["gitlab"]["data"]["method"] == "search_code"
    assert result["remotes"]["azdo"]["data"]["kwargs"]["repos"] == ["repo-a"]


def test_execute_code_search_all_skips_unconfigured_remotes(monkeypatch: Any) -> None:
    runtime = make_runtime_config(azdo_org="", github_org="")
    calls = _install_client_fakes(monkeypatch, runtime)
    client = SmithClient(session=object(), smith_config=_make_smith_config(runtime))

    result = client.execute_code_search(
        remote_or_provider="all",
        query="grafana",
        project=None,
        repos=None,
        skip=0,
        take=5,
    )

    assert calls["run_fanout"] == {"remotes": ["gitlab"], "requested_remote": "all"}
    assert result["summary"]["queried"] == ["gitlab"]
    assert set(result["remotes"]) == {"gitlab"}
    assert _FakeAzdoProvider.instances == []
    assert _FakeGitHubProvider.instances == []
    assert len(_FakeGitLabProvider.instances) == 1
    assert _FakeGitLabProvider.instances[0].kwargs["gitlab_org"] == "gitlab-org"


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_execute_pr_list_uses_projects_as_repo_fallback(monkeypatch: Any, provider: str) -> None:
    runtime = make_runtime_config()
    _install_client_fakes(monkeypatch, runtime)
    client = SmithClient(session=object(), smith_config=_make_smith_config(runtime))

    result = client.execute_pr_list(
        remote_or_provider=provider,
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

    assert result["remotes"][provider]["data"]["kwargs"]["repos"] == ["repo-from-project"]

def test_canonical_methods_cover_removed_wrapper_behavior(monkeypatch: Any) -> None:
    runtime = make_runtime_config()
    _install_client_fakes(monkeypatch, runtime)
    client = SmithClient(session=object(), smith_config=_make_smith_config(runtime))

    discover_projects = client.execute_discover_projects(remote_or_provider="gitlab")
    ci_logs = client.execute_ci_logs(remote_or_provider="gitlab", project=None, repo="repo-a", build_id=1)
    work_get = client.execute_work_get(remote_or_provider="gitlab", project=None, repo="repo-a", work_item_id=2)

    assert discover_projects["remotes"]["gitlab"]["data"]["method"] == "list_projects"
    assert ci_logs["remotes"]["gitlab"]["data"]["method"] == "get_build_log"
    assert work_get["remotes"]["gitlab"]["data"]["method"] == "get_ticket_by_id"
