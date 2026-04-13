from __future__ import annotations

from typing import Any

import pytest
import requests
from tests.support import make_runtime_config

from smith.providers.azdo import AzdoProvider


def _provider(config: Any | None = None, credential: Any | None = None) -> AzdoProvider:
    return AzdoProvider(config=config or make_runtime_config(), credential=credential, session=requests.Session())


def test_azdo_list_project_repository_and_search_code_views(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []

    def _fake_request_json(
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        calls.append({"method": method, "url": url, "params": params, "json_body": json_body})
        if url.endswith("/_apis/projects"):
            return {"value": [{"id": "1", "name": "proj-a", "state": "wellFormed", "url": "https://dev.azure.com/acme/_apis/projects/1"}]}
        if url.endswith("/proj-a/_apis/git/repositories"):
            return {"value": [{"id": "2", "name": "repo-a", "defaultBranch": "refs/heads/main", "webUrl": "https://dev.azure.com/acme/proj-a/_git/repo-a"}]}
        return {
            "count": 1,
            "results": [{"project": {"name": "proj-a"}, "repository": {"name": "repo-a"}, "path": "/src/app.py"}],
        }

    monkeypatch.setattr(provider, "_request_json", _fake_request_json)

    assert provider.list_projects() == [
        {
            "id": "1",
            "name": "proj-a",
            "state": "wellFormed",
            "url": "https://dev.azure.com/acme/_apis/projects/1",
        }
    ]
    assert provider.list_repositories("proj-a") == [
        {
            "id": "2",
            "name": "repo-a",
            "defaultBranch": "refs/heads/main",
            "webUrl": "https://dev.azure.com/acme/proj-a/_git/repo-a",
        }
    ]
    search = provider.search_code(query="grafana", project="proj-a", repos=["repo-a"], skip=2, take=5)

    assert search == {"matchesCount": 1, "results": ["proj-a/repo-a:/src/app.py"]}
    assert calls[-1]["json_body"] == {
        "searchText": "grafana",
        "$skip": 2,
        "$top": 5,
        "filters": {"Project": ["proj-a"], "Repository": ["repo-a"]},
        "$orderBy": [{"field": "filename", "sortOrder": "ASC"}],
    }

    with pytest.raises(ValueError, match="Repository filter requires --project"):
        provider.search_code(query="grafana", project=None, repos=["repo-a"])


def test_azdo_grep_supports_match_all_shortcut_and_warning_paths(monkeypatch: Any) -> None:
    provider = _provider(make_runtime_config(max_output_chars=50))
    repo_url = f"{provider.org_url}/proj-a/_apis/git/repositories/repo-a/items"

    def _fake_request_json(method: str, url: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        assert url == repo_url
        if params and params.get("scopePath") in {"/src", "/"}:
            return {
                "value": [
                    {"path": "/src/app.py", "gitObjectType": "blob", "contentMetadata": {"isBinary": False}},
                    {"path": "/src/util.py", "gitObjectType": "blob", "contentMetadata": {"isBinary": False}},
                ]
            }
        if params and params.get("path") == "/src/app.py":
            return {"content": "ok\nerror"}
        if params and params.get("path") == "/src/util.py":
            raise RuntimeError("denied")
        raise AssertionError(f"unexpected request: {url} {params}")

    monkeypatch.setattr(provider, "_request_json", _fake_request_json)

    shortcut = provider.grep(project="proj-a", repo="repo-a", pattern=".*", path="/src", glob="*.py", output_mode="files_with_matches")
    assert shortcut == {
        "text": "/src/app.py\n/src/util.py",
        "files_matched": 2,
        "warnings": [],
        "partial": False,
    }

    result = provider.grep(project="proj-a", repo="repo-a", pattern="error", output_mode="count", case_insensitive=False, context_lines=0)

    assert result["text"] == "/src/app.py:1"
    assert result["files_matched"] == 1
    assert result["warnings"] == ["failed to read /src/util.py: denied"]
    assert result["partial"] is True


def test_azdo_grep_returns_guard_result_before_reading_large_scopes(monkeypatch: Any) -> None:
    provider = _provider(make_runtime_config(grep_max_files=1))
    repo_url = f"{provider.org_url}/proj-a/_apis/git/repositories/repo-a/items"

    def _fake_request_json(method: str, url: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        assert url == repo_url
        if params and params.get("scopePath") == "/":
            return {
                "value": [
                    {"path": "/src/app.py", "gitObjectType": "blob", "contentMetadata": {"isBinary": False}},
                    {"path": "/src/util.py", "gitObjectType": "blob", "contentMetadata": {"isBinary": False}},
                ]
            }
        raise AssertionError(f"unexpected request: {url} {params}")

    monkeypatch.setattr(provider, "_request_json", _fake_request_json)
    monkeypatch.setattr(
        provider,
        "_get_file_text",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not read file content")),
    )

    result = provider.grep(project="proj-a", repo="repo-a", pattern="error")

    assert result["files_matched"] == 0
    assert result["partial"] is True
    assert result["warnings"] == [
        "candidate file count 2 exceeds SMITH_GREP_MAX_FILES=1; narrow with --path/--glob or start with `smith code search`."
    ]
    assert "Search scope contains 2 candidate files which exceeds the safety limit (1)." in result["text"]
    assert 'smith code search "<query>"' in result["text"]


def test_azdo_list_pull_requests_maps_filters_statuses_and_labels(monkeypatch: Any) -> None:
    provider = _provider()
    active_pr = {
        "pullRequestId": 1,
        "title": "Active PR",
        "isDraft": False,
        "creationDate": "2025-01-10T00:00:00Z",
        "closedDate": None,
        "createdBy": {"displayName": "alice", "uniqueName": "alice@example.com"},
        "repository": {"name": "repo-a", "id": "repo-id"},
        "sourceRefName": "refs/heads/feature",
        "targetRefName": "refs/heads/main",
        "labels": [{"name": "bug"}],
    }
    completed_pr = {
        "pullRequestId": 2,
        "title": "Completed PR",
        "isDraft": False,
        "creationDate": "2025-01-09T00:00:00Z",
        "closedDate": "2025-01-10T00:00:00Z",
        "createdBy": {"displayName": "bob", "uniqueName": "bob@example.com"},
        "repository": {"name": "repo-a", "id": "repo-id"},
        "sourceRefName": "refs/heads/feature-two",
        "targetRefName": "refs/heads/main",
        "labels": [{"name": "enhancement"}],
    }
    abandoned_pr = {
        "pullRequestId": 3,
        "title": "Abandoned PR",
        "isDraft": False,
        "creationDate": "2025-01-08T00:00:00Z",
        "closedDate": "2025-01-09T00:00:00Z",
        "createdBy": {"displayName": "carol", "uniqueName": "carol@example.com"},
        "repository": {"name": "repo-a", "id": "repo-id"},
        "sourceRefName": "refs/heads/feature-three",
        "targetRefName": "refs/heads/main",
        "labels": [],
    }
    calls: list[dict[str, Any]] = []

    def _fake_request_json(method: str, url: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        calls.append({"url": url, "params": params})
        status = (params or {}).get("searchCriteria.status")
        if status == "active":
            return {"value": [active_pr]}
        if status == "completed":
            return {"value": [completed_pr]}
        if status == "abandoned":
            return {"value": [abandoned_pr]}
        return {"value": []}

    monkeypatch.setattr(provider, "_request_json", _fake_request_json)

    result = provider.list_pull_requests(
        projects=["proj-a"],
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
    assert calls[0]["params"]["searchCriteria.includeLabels"] == "true"


def test_azdo_search_pull_requests_filters_query_and_maps_results(monkeypatch: Any) -> None:
    provider = _provider()
    active_pr = {
        "pullRequestId": 1,
        "title": "Rollout fix",
        "description": "Fix the rollout flow",
        "isDraft": False,
        "creationDate": "2025-01-10T00:00:00Z",
        "closedDate": None,
        "createdBy": {"displayName": "alice", "uniqueName": "alice@example.com"},
        "repository": {"name": "repo-a", "id": "repo-id"},
        "sourceRefName": "refs/heads/feature",
        "targetRefName": "refs/heads/main",
        "labels": [{"name": "bug"}],
    }
    completed_pr = {
        "pullRequestId": 2,
        "title": "Completed change",
        "description": "rollout completed successfully",
        "isDraft": False,
        "creationDate": "2025-01-09T00:00:00Z",
        "closedDate": "2025-01-10T00:00:00Z",
        "createdBy": {"displayName": "bob", "uniqueName": "bob@example.com"},
        "repository": {"name": "repo-a", "id": "repo-id"},
        "sourceRefName": "refs/heads/feature-two",
        "targetRefName": "refs/heads/main",
        "labels": [{"name": "enhancement"}],
    }
    unrelated_pr = {
        "pullRequestId": 3,
        "title": "Refactor",
        "description": "cleanup only",
        "isDraft": False,
        "creationDate": "2025-01-08T00:00:00Z",
        "closedDate": "2025-01-09T00:00:00Z",
        "createdBy": {"displayName": "carol", "uniqueName": "carol@example.com"},
        "repository": {"name": "repo-a", "id": "repo-id"},
        "sourceRefName": "refs/heads/feature-three",
        "targetRefName": "refs/heads/main",
        "labels": [],
    }

    def _fake_request_json(method: str, url: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        status = (params or {}).get("searchCriteria.status")
        if status == "active":
            return {"value": [active_pr]}
        if status == "completed":
            return {"value": [completed_pr]}
        if status == "abandoned":
            return {"value": [unrelated_pr]}
        return {"value": []}

    monkeypatch.setattr(provider, "_request_json", _fake_request_json)

    result = provider.search_pull_requests(
        query="rollout",
        project="proj-a",
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

    assert result == {
        "returned_count": 2,
        "has_more": False,
        "results": [
            {
                "pr_id": 1,
                "title": "Rollout fix",
                "created_by": "alice",
                "status": "active",
                "creation_date": "2025-01-10T00:00:00Z",
                "project_name": "proj-a",
                "repository_name": "repo-a",
                "repository_id": "repo-id",
                "closed_date": None,
                "source_branch": "feature",
                "target_branch": "main",
                "target_ref": "refs/heads/main",
                "labels": ["bug"],
            },
            {
                "pr_id": 2,
                "title": "Completed change",
                "created_by": "bob",
                "status": "completed",
                "creation_date": "2025-01-09T00:00:00Z",
                "project_name": "proj-a",
                "repository_name": "repo-a",
                "repository_id": "repo-id",
                "closed_date": "2025-01-10",
                "source_branch": "feature-two",
                "target_branch": "main",
                "target_ref": "refs/heads/main",
                "labels": ["enhancement"],
            },
        ],
    }


def test_azdo_search_pull_requests_requires_project_for_repo_filters() -> None:
    provider = _provider()

    with pytest.raises(ValueError, match="Repository filter requires --project"):
        provider.search_pull_requests(
            query="rollout",
            project=None,
            repos=["repo-a"],
            statuses=["active"],
            creators=None,
            date_from=None,
            date_to=None,
            skip=0,
            take=10,
            exclude_drafts=False,
            include_labels=False,
        )


def test_azdo_search_pull_requests_dedupes_repo_filters(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []
    active_pr = {
        "pullRequestId": 1,
        "title": "Rollout fix",
        "description": "Fix the rollout flow",
        "isDraft": False,
        "creationDate": "2025-01-10T00:00:00Z",
        "closedDate": None,
        "createdBy": {"displayName": "alice", "uniqueName": "alice@example.com"},
        "repository": {"name": "repo-a", "id": "repo-id"},
        "sourceRefName": "refs/heads/feature",
        "targetRefName": "refs/heads/main",
        "labels": [],
    }

    def _fake_request_json(method: str, url: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        calls.append({"method": method, "url": url, "params": params})
        return {"value": [active_pr]}

    monkeypatch.setattr(provider, "_request_json", _fake_request_json)

    result = provider.search_pull_requests(
        query="rollout",
        project="proj-a",
        repos=["repo-a", "repo-a"],
        statuses=["active"],
        creators=None,
        date_from=None,
        date_to=None,
        skip=0,
        take=10,
        exclude_drafts=False,
        include_labels=False,
    )

    assert result["returned_count"] == 1
    assert calls == [
        {
            "method": "GET",
            "url": f"{provider.org_url}/proj-a/_apis/git/repositories/repo-a/pullrequests",
            "params": {
                "api-version": provider.api_version,
                "searchCriteria.status": "active",
                "$top": 10,
                "$skip": 0,
                "searchCriteria.queryTimeRangeType": "created",
            },
        }
    ]


def test_azdo_pull_request_views_build_logs_and_grep(monkeypatch: Any) -> None:
    provider = _provider()

    def _fake_request_json(method: str, url: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        if url.endswith("/pullrequests/17"):
            return {"pullRequestId": 17, "title": "Fix", "status": "active"}
        if url.endswith("/pullrequests/17/threads"):
            return {
                "value": [
                    {
                        "id": 1,
                        "status": "active",
                        "threadContext": {
                            "filePath": "/src/app.py",
                            "rightFileStart": {"line": 10},
                            "rightFileEnd": {"line": 12},
                        },
                        "comments": [
                            {
                                "id": 2,
                                "author": {"displayName": "alice"},
                                "content": "Looks good",
                                "commentType": "text",
                                "isDeleted": False,
                                "publishedDate": "2025-01-01T00:00:00Z",
                                "lastUpdatedDate": "2025-01-01T00:00:00Z",
                            }
                        ],
                        "publishedDate": "2025-01-01T00:00:00Z",
                        "lastUpdatedDate": "2025-01-01T00:00:00Z",
                    }
                ]
            }
        if url.endswith("/pullrequests/17/iterations"):
            return {"value": [{"id": 1}, {"id": 2}]}
        if url.endswith("/pullrequests/17/iterations/2/changes"):
            return {"changeEntries": [{"item": {"path": "/src/app.py"}}, {"item": {"path": "/src/util.py"}}]}
        if url.endswith("/builds/55/logs"):
            return {"value": [{"id": 88, "type": "container", "createdOn": "2025-01-01T00:00:00Z", "lineCount": 12, "url": "https://log"}]}
        if url.endswith("/builds/55"):
            return {
                "buildNumber": "2025.01.01.1",
                "status": "completed",
                "result": "failed",
                "definition": {"name": "CI"},
                "repository": {"name": "repo-a"},
                "sourceBranch": "refs/heads/main",
                "sourceVersion": "abc123",
            }
        raise AssertionError(f"unexpected request: {url} {params}")

    monkeypatch.setattr(provider, "_request_json", _fake_request_json)

    pr = provider.get_pull_request(project="proj-a", repo="repo-a", pull_request_id=17)
    threads = provider.get_pull_request_threads(project="proj-a", repo="repo-a", pull_request_id=17)
    build = provider.get_build_log(project="proj-a", build_id=55)

    assert pr["pull_request"] == {"pullRequestId": 17, "title": "Fix", "status": "active"}
    assert pr["changed_files"] == ["/src/app.py", "/src/util.py"]
    assert pr["threads"][0]["id"] == 1
    assert pr["threads"][0]["comments"][0]["author"] == {"displayName": "alice"}
    assert threads["returned_count"] == 1
    assert threads["total_comments"] == 1
    assert threads["threads"][0]["file_path"] == "/src/app.py"
    assert build == {
        "metadata": {
            "project_name": "proj-a",
            "build_id": 55,
            "build_number": "2025.01.01.1",
            "status": "completed",
            "result": "failed",
            "definition_name": "CI",
            "repository_name": "repo-a",
            "branch": "main",
            "commit": "abc123",
        },
        "logs": [{"id": 88, "type": "container", "created_on": "2025-01-01T00:00:00Z", "line_count": 12, "url": "https://log"}],
    }

    monkeypatch.setattr(provider, "get_build_log", lambda **kwargs: {"logs": [{"id": 88}, {"id": 89}]})
    monkeypatch.setattr(provider, "get_build_log_content", lambda *, log_id, **kwargs: "ok\nerror" if log_id == 89 else "ok")
    grep = provider.grep_build_log(project="proj-a", build_id=55, pattern="error", output_mode="logs_with_matches")

    assert grep == {
        "text": "Log 89",
        "logs_matched": 1,
        "warnings": [],
        "partial": False,
    }


def test_azdo_work_item_views_and_cross_project_mine_aggregation(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []

    def _fake_request_json(
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        calls.append({"url": url, "params": params, "json_body": json_body})
        if url.endswith("/wit/wiql"):
            return {"workItems": [{"id": 1}, {"id": 2}, {"id": 3}]}
        if url.endswith("/wit/workitems") and params and "ids" in params:
            ids = params["ids"].split(",")
            return {"value": [{"id": int(item_id), "fields": {"System.Title": f"Item {item_id}"}} for item_id in ids]}
        if "workitemsearchresults" in url:
            return {
                "count": 2,
                "results": [
                    {
                        "fields": {
                            "System.Id": 10,
                            "System.Title": "Incident",
                            "System.State": "Active",
                            "System.WorkItemType": "Bug",
                            "System.TeamProject": "proj-a",
                            "System.AssignedTo": {"displayName": "alice"},
                            "System.Tags": "sev1; grafana",
                            "System.CreatedDate": "2025-01-01T00:00:00Z",
                            "System.ChangedDate": "2025-01-02T00:00:00Z",
                        },
                        "url": "https://dev.azure.com/acme/_workitems/edit/10",
                        "hits": [{"text": "matched text"}, "extra"],
                    }
                ],
            }
        raise AssertionError(f"unexpected request: {url}")

    monkeypatch.setattr(provider, "_request_json", _fake_request_json)

    listed = provider.list_work_items(project="proj-a", wiql="SELECT [System.Id] FROM WorkItems", skip=1, take=2)
    searched = provider.search_work_items(
        query="incident",
        project="proj-a",
        area="Ops",
        work_item_type="Bug",
        state="Active",
        assigned_to="alice",
        skip=0,
        take=1,
    )

    assert listed == {
        "total_count": 3,
        "returned_count": 2,
        "has_more": False,
        "results": [
            {"id": 2, "fields": {"System.Title": "Item 2"}},
            {"id": 3, "fields": {"System.Title": "Item 3"}},
        ],
    }
    assert searched["results"][0] == {
        "id": 10,
        "title": "Incident",
        "state": "Active",
        "type": "Bug",
        "project": "proj-a",
        "assigned_to": "alice",
        "tags": ["sev1", "grafana"],
        "created_date": "2025-01-01T00:00:00Z",
        "changed_date": "2025-01-02T00:00:00Z",
        "url": "https://dev.azure.com/acme/_workitems/edit/10",
        "highlights": ["matched text", "extra"],
    }
    assert calls[-1]["json_body"] == {
        "searchText": "incident",
        "$skip": 0,
        "$top": 1,
        "filters": {
            "System.TeamProject": ["proj-a"],
            "System.AreaPath": ["Ops"],
            "System.WorkItemType": ["Bug"],
            "System.State": ["Active"],
            "System.AssignedTo": ["alice"],
        },
    }

    project_forwarded: dict[str, Any] = {}
    monkeypatch.setattr(
        provider,
        "list_work_items",
        lambda **kwargs: project_forwarded.update(kwargs) or {"returned_count": 1, "has_more": False, "results": [{"id": 1}]},
    )
    project_result = provider.get_my_work_items(project="proj-a", include_closed=False, skip=2, take=3)

    assert "WHERE [System.TeamProject] = 'proj-a'" in project_forwarded["wiql"]
    assert "[System.AssignedTo] = @Me" in project_forwarded["wiql"]
    assert "NOT IN ('Closed', 'Done', 'Removed')" in project_forwarded["wiql"]
    assert project_result == {"returned_count": 1, "has_more": False, "results": [{"id": 1}]}

    monkeypatch.setattr(provider, "list_projects", lambda: [{"name": "proj-a"}, {"name": "proj-b"}])

    def _list_work_items(**kwargs: Any) -> Any:
        if kwargs["project"] == "proj-a":
            return {"results": [{"id": 7}]}
        raise RuntimeError("API unavailable")

    monkeypatch.setattr(provider, "list_work_items", _list_work_items)
    aggregate = provider.get_my_work_items(project=None, include_closed=True, skip=0, take=5)

    assert aggregate == {
        "returned_count": 1,
        "has_more": False,
        "results": [{"id": 7}],
        "warnings": ["proj-b: API unavailable"],
    }
