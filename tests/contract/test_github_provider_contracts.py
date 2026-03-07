from __future__ import annotations

from typing import Any

import requests
from tests.support import make_runtime_config

from smith.providers.github import GitHubProvider


def _provider(config: Any | None = None) -> GitHubProvider:
    return GitHubProvider(config=config or make_runtime_config(), session=requests.Session())


def test_github_maps_project_repository_views(monkeypatch: Any) -> None:
    provider = _provider()
    monkeypatch.setattr(
        provider,
        "_get_paginated_list",
        lambda path, **kwargs: [
            {"id": 1, "name": "repo-a", "default_branch": "main", "html_url": "https://github.com/octo-org/repo-a"}
        ],
    )

    assert provider.list_projects() == [
        {
            "id": "octo-org",
            "name": "octo-org",
            "state": "active",
            "url": "https://github.com/octo-org",
        }
    ]
    assert provider.list_repositories() == [
        {
            "id": 1,
            "name": "repo-a",
            "defaultBranch": "main",
            "webUrl": "https://github.com/octo-org/repo-a",
        }
    ]


def test_github_search_code_builds_repo_qualifier_and_applies_skip_take(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []

    def _fake_request_json(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        calls.append({"method": method, "path": path, "params": params})
        return {
            "total_count": 2,
            "items": [
                {"repository": {"name": "repo-a"}, "path": "src/app.py"},
                {"repository": {"name": "repo-a"}, "path": "src/util.py"},
            ],
        }

    monkeypatch.setattr(provider, "_request_json", _fake_request_json)

    result = provider.search_code(query="grafana", project="repo-a", repos=None, skip=1, take=1)

    assert result == {"matchesCount": 2, "results": ["octo-org/repo-a:/src/util.py"]}
    assert calls == [
        {
            "method": "GET",
            "path": "/search/code",
            "params": {"q": "grafana repo:octo-org/repo-a", "per_page": 2, "page": 1},
        }
    ]


def test_github_grep_supports_match_all_shortcut_compile_errors_and_warning_paths(monkeypatch: Any) -> None:
    provider = _provider(make_runtime_config(max_output_chars=50))
    monkeypatch.setenv("GITHUB_GREP_ENABLE_PARALLEL", "false")
    repo_prefix = provider._repo_prefix("repo-a")

    def _fake_request_json(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        if path == repo_prefix:
            return {"default_branch": "main"}
        if path == f"{repo_prefix}/git/trees/main" and params == {"recursive": "1"}:
            return {
                "tree": [
                    {"path": "src/app.py", "type": "blob", "sha": "sha-app"},
                    {"path": "src/util.py", "type": "blob", "sha": "sha-util"},
                ]
            }
        if path == f"{repo_prefix}/git/blobs/sha-app":
            return {
                "content": "b2sKZXJyb3IKZXJyb3I=",
                "encoding": "base64",
            }
        if path == f"{repo_prefix}/git/blobs/sha-util":
            raise RuntimeError("blob missing")
        if path == f"{repo_prefix}/contents/src/util.py":
            raise RuntimeError("denied")
        raise AssertionError(f"unexpected request: {path} {params}")

    monkeypatch.setattr(provider, "_request_json", _fake_request_json)

    shortcut = provider.grep(repo="repo-a", pattern=".*", output_mode="files_with_matches")
    assert shortcut == {
        "text": "/src/app.py\n/src/util.py",
        "files_matched": 2,
        "warnings": [],
        "partial": False,
    }

    result = provider.grep(repo="repo-a", pattern="error", output_mode="count", case_insensitive=False, context_lines=0)

    assert result["text"] == "/src/app.py:2"
    assert result["files_matched"] == 1
    assert result["warnings"] == ["failed to read /src/util.py: denied"]
    assert result["partial"] is True

    compile_error = provider.grep(repo="repo-a", pattern="[")
    assert compile_error["text"].startswith("Error: Invalid regex pattern")


def test_github_list_pull_requests_maps_statuses_filters_and_labels(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []

    open_pull = {
        "number": 1,
        "title": "Active PR",
        "state": "open",
        "draft": False,
        "user": {"login": "alice"},
        "created_at": "2025-01-10T00:00:00Z",
        "closed_at": None,
        "merged_at": None,
        "head": {"ref": "feature/one"},
        "base": {"ref": "main"},
        "labels": [{"name": "bug"}],
        "id": 1001,
    }
    merged_pull = {
        "number": 2,
        "title": "Merged PR",
        "state": "closed",
        "draft": False,
        "user": {"login": "bob"},
        "created_at": "2025-01-08T00:00:00Z",
        "closed_at": "2025-01-09T00:00:00Z",
        "merged_at": "2025-01-09T00:00:00Z",
        "head": {"ref": "feature/two"},
        "base": {"ref": "main"},
        "labels": [{"name": "enhancement"}],
        "id": 1002,
    }
    abandoned_pull = {
        "number": 3,
        "title": "Abandoned PR",
        "state": "closed",
        "draft": False,
        "user": {"login": "carol"},
        "created_at": "2025-01-07T00:00:00Z",
        "closed_at": "2025-01-08T00:00:00Z",
        "merged_at": None,
        "head": {"ref": "feature/three"},
        "base": {"ref": "main"},
        "labels": [],
        "id": 1003,
    }

    def _fake_request(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        calls.append({"method": method, "path": path, "params": params})
        state = (params or {}).get("state")
        if state == "open":
            return [open_pull]
        if state == "closed":
            return [merged_pull, abandoned_pull]
        return []

    monkeypatch.setattr(provider, "_request", _fake_request)

    result = provider.list_pull_requests(
        repos=["repo-a"],
        statuses=["active", "completed", "abandoned"],
        creators=None,
        date_from=None,
        date_to=None,
        skip=0,
        take=10,
        exclude_drafts=True,
        include_labels=True,
    )

    assert [entry["status"] for entry in result["results"]] == ["active", "completed", "abandoned"]
    assert result["results"][0]["labels"] == ["bug"]
    assert result["results"][1]["labels"] == ["enhancement"]
    assert result["returned_count"] == 3
    assert result["has_more"] is False
    assert calls[0]["params"]["state"] == "open"
    assert calls[1]["params"]["state"] == "closed"


def test_github_get_pull_request_and_threads_map_review_data(monkeypatch: Any) -> None:
    provider = _provider()
    monkeypatch.setattr(
        provider,
        "_request_json",
        lambda method, path, **kwargs: {
            "number": 17,
            "title": "Fix it",
            "state": "open",
            "user": {"login": "alice"},
            "head": {"ref": "feature"},
            "base": {"ref": "main"},
        },
    )
    monkeypatch.setattr(
        provider,
        "_get_paginated_list",
        lambda path, **kwargs: [{"filename": "src/app.py"}, {"filename": "src/util.py"}],
    )
    monkeypatch.setattr(provider, "get_pull_request_threads", lambda **kwargs: {"threads": [{"id": "review-1"}]})

    result = provider.get_pull_request(repo="repo-a", pull_request_id=17)

    assert result == {
        "pull_request": {
            "pullRequestId": 17,
            "title": "Fix it",
            "status": "active",
            "createdBy": {"displayName": "alice"},
            "sourceRefName": "refs/heads/feature",
            "targetRefName": "refs/heads/main",
        },
        "threads": [{"id": "review-1"}],
        "changed_files": ["src/app.py", "src/util.py"],
    }

    thread_provider = _provider()
    issue_comments = [
        {
            "id": 1,
            "user": {"login": "alice"},
            "body": "General note",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
        }
    ]
    review_comments = [
        {
            "id": 2,
            "user": {"login": "bob"},
            "body": "Inline",
            "path": "src/app.py",
            "line": 18,
            "created_at": "2025-01-02T00:00:00Z",
            "updated_at": "2025-01-02T00:00:00Z",
        },
        {
            "id": 3,
            "user": {"login": "eve"},
            "body": "Deleted",
            "path": "src/app.py",
            "line": 20,
            "isDeleted": True,
            "created_at": "2025-01-03T00:00:00Z",
            "updated_at": "2025-01-03T00:00:00Z",
        },
    ]
    monkeypatch.setattr(
        thread_provider,
        "_get_paginated_list",
        lambda path, **kwargs: issue_comments if "/issues/" in path else review_comments,
    )

    threads = thread_provider.get_pull_request_threads(repo="repo-a", pull_request_id=17)

    assert threads["returned_count"] == 2
    assert threads["total_comments"] == 2
    assert threads["threads"][0]["id"] == "issue-1"
    assert threads["threads"][1]["file_path"] == "/src/app.py"
    assert threads["threads"][1]["line_start"] == 18


def test_github_build_log_and_build_grep(monkeypatch: Any) -> None:
    provider = _provider()
    responses = {
        f"{provider._repo_prefix('repo-a')}/actions/runs/55": {
            "run_number": 12,
            "status": "completed",
            "conclusion": "success",
            "name": "CI",
            "head_branch": "main",
            "head_sha": "abc123",
        },
        f"{provider._repo_prefix('repo-a')}/actions/runs/55/jobs": {
            "jobs": [{"id": 88, "started_at": "2025-01-01T00:00:00Z", "name": "linux", "url": "https://job"}]
        },
    }
    monkeypatch.setattr(provider, "_request_json", lambda method, path, **kwargs: responses[path])

    build_log = provider.get_build_log(repo="repo-a", build_id=55)
    assert build_log == {
        "metadata": {
            "project_name": "octo-org",
            "build_id": 55,
            "build_number": 12,
            "status": "completed",
            "result": "success",
            "definition_name": "CI",
            "repository_name": "repo-a",
            "branch": "main",
            "commit": "abc123",
        },
        "logs": [
            {
                "id": 88,
                "type": "job",
                "created_on": "2025-01-01T00:00:00Z",
                "line_count": None,
                "url": "https://job",
                "stage_name": "linux",
                "job_name": "linux",
                "step_name": None,
            }
        ],
    }

    monkeypatch.setattr(provider, "get_build_log", lambda **kwargs: {"logs": [{"id": 88}, {"id": 89}]})
    monkeypatch.setattr(provider, "get_build_log_content", lambda *, log_id, **kwargs: "ok\nerror" if log_id == 89 else "ok")
    grep = provider.grep_build_log(repo="repo-a", build_id=55, pattern="error", output_mode="logs_with_matches")

    assert grep == {
        "text": "Log 89",
        "logs_matched": 1,
        "warnings": [],
        "partial": False,
    }


def test_github_issue_search_ticket_mapping_and_my_work_items(monkeypatch: Any) -> None:
    provider = _provider()
    captured_params: list[dict[str, Any]] = []

    def _fake_request_json(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        captured_params.append({"path": path, "params": params})
        if path.startswith("/search/issues"):
            return {
                "total_count": 2,
                "items": [
                    {
                        "number": 10,
                        "title": "Incident",
                        "state": "open",
                        "assignee": {"login": "alice"},
                        "labels": [{"name": "sev1"}],
                        "created_at": "2025-01-01T00:00:00Z",
                        "updated_at": "2025-01-02T00:00:00Z",
                        "html_url": "https://github.com/octo-org/repo-a/issues/10",
                        "repository_url": "https://api.github.com/repos/octo-org/repo-a",
                    }
                ],
            }
        return {
            "number": 10,
            "html_url": "https://github.com/octo-org/repo-a/issues/10",
            "state": "closed",
            "title": "Incident",
        }

    monkeypatch.setattr(provider, "_request_json", _fake_request_json)

    search = provider.search_work_items(
        query="incident",
        project="repo-a",
        repo=None,
        state="open",
        assigned_to="alice",
        skip=0,
        take=1,
        include_closed=False,
    )
    ticket = provider.get_ticket_by_id(repo="repo-a", work_item_id=10)

    assert "org:octo-org" in captured_params[0]["params"]["q"]
    assert "repo:octo-org/repo-a" in captured_params[0]["params"]["q"]
    assert "is:open" in captured_params[0]["params"]["q"]
    assert "assignee:alice" in captured_params[0]["params"]["q"]
    assert search["results"][0]["tags"] == ["sev1"]
    assert search["results"][0]["assigned_to"] == "alice"
    assert ticket == {
        "id": 10,
        "url": "https://github.com/octo-org/repo-a/issues/10",
        "fields": {
            "System.WorkItemType": "Issue",
            "System.State": "Closed",
            "System.Title": "Incident",
        },
    }

    forwarded: dict[str, Any] = {}
    monkeypatch.setattr(
        provider,
        "search_work_items",
        lambda **kwargs: forwarded.update(kwargs) or {"returned_count": 1, "has_more": False, "results": [{"id": 10}]},
    )
    mine = provider.get_my_work_items(project="repo-a", repo=None, include_closed=True, skip=2, take=3)

    assert forwarded == {
        "query": "",
        "project": "repo-a",
        "repo": None,
        "assigned_to": "@me",
        "skip": 2,
        "take": 3,
        "include_closed": True,
    }
    assert mine == {
        "returned_count": 1,
        "has_more": False,
        "results": [{"id": 10}],
        "warnings": [],
    }
