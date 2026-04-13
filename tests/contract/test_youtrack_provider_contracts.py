from __future__ import annotations

from typing import Any

import requests
from tests.support import (
    GitHubApiStubServer,
    StubRequest,
    StubResponse,
    make_runtime_config,
)

from smith.providers.youtrack import YouTrackProvider


def _provider(*, base_url: str) -> YouTrackProvider:
    return YouTrackProvider(
        config=make_runtime_config(),
        session=requests.Session(),
        youtrack_api_url=f"{base_url}/api",
    )


def test_youtrack_get_ticket_by_id_returns_rich_shape(monkeypatch: Any) -> None:
    def _issue_route(request: StubRequest) -> StubResponse:
        assert request.query["fields"]
        return StubResponse(
            status=200,
            body={
                "id": "78-1",
                "idReadable": "RAD-1055",
                "summary": "Investigate patching flow",
                "description": "Ticket body\n![](image1.png)",
                "created": 1_777_000_000_000,
                "updated": 1_777_100_000_000,
                "resolved": None,
                "votes": 7,
                "commentsCount": 1,
                "project": {"id": "73-302", "name": "Release and Deployments", "shortName": "RAD"},
                "reporter": {"id": "26-1", "login": "fausto", "fullName": "Fausto Suarez Rosario"},
                "updater": {"id": "26-2", "login": "alice", "fullName": "Alice"},
                "tags": [{"name": "patching"}],
                "customFields": [
                    {"id": "74-1", "name": "Type", "$type": "SingleEnumIssueCustomField", "value": {"name": "Task"}},
                    {"id": "74-2", "name": "State", "$type": "StateIssueCustomField", "value": {"name": "In Progress"}},
                    {"id": "74-3", "name": "Priority", "$type": "SingleEnumIssueCustomField", "value": {"name": "Normal"}},
                    {"id": "74-4", "name": "Impact", "$type": "SingleEnumIssueCustomField", "value": {"name": "4"}},
                ],
            },
        )

    def _attachments_route(request: StubRequest) -> StubResponse:
        assert request.query["fields"]
        return StubResponse(
            status=200,
            body=[
                {
                    "id": "104-1",
                    "name": "image1.png",
                    "created": 1_777_000_100_000,
                    "size": 123,
                    "mimeType": "image/png",
                    "url": "/api/files/1",
                    "thumbnailURL": "/api/files/2",
                    "author": {"login": "fausto", "fullName": "Fausto Suarez Rosario"},
                },
                {
                    "id": "104-2",
                    "name": "notes.txt",
                    "created": 1_777_000_200_000,
                    "size": 99,
                    "mimeType": "text/plain",
                    "url": "/api/files/3",
                    "thumbnailURL": None,
                    "author": {"login": "fausto", "fullName": "Fausto Suarez Rosario"},
                },
            ],
        )

    def _comments_route(request: StubRequest) -> StubResponse:
        assert request.query["fields"]
        return StubResponse(
            status=200,
            body=[
                {
                    "id": "102-1",
                    "text": "Looks good ![](comment-image.png)",
                    "created": 1_777_000_300_000,
                    "updated": None,
                    "deleted": False,
                    "pinned": False,
                    "author": {"login": "alice", "fullName": "Alice"},
                    "reactions": [
                        {
                            "id": "236-1",
                            "reaction": "thumbs-up",
                            "author": {"login": "fausto", "fullName": "Fausto Suarez Rosario"},
                        }
                    ],
                    "attachments": [
                        {
                            "id": "104-3",
                            "name": "comment-image.png",
                            "created": 1_777_000_300_000,
                            "size": 55,
                            "mimeType": "image/png",
                            "url": "/api/files/4",
                            "thumbnailURL": "/api/files/5",
                            "author": {"login": "alice", "fullName": "Alice"},
                        }
                    ],
                }
            ],
        )

    def _links_route(request: StubRequest) -> StubResponse:
        assert request.query["fields"]
        return StubResponse(
            status=200,
            body=[
                {
                    "id": "51-0",
                    "direction": "BOTH",
                    "linkType": {"id": "51-0", "name": "Relates"},
                    "issues": [{"id": "78-2", "idReadable": "RAD-1004", "summary": "Implement endpoint"}],
                }
            ],
        )

    def _activities_route(request: StubRequest) -> StubResponse:
        assert request.query["fields"]
        assert "IssueCreatedCategory" in request.query["categories"]
        return StubResponse(
            status=200,
            body={
                "activities": [
                    {
                        "id": "78-1.0-0",
                        "$type": "IssueCreatedActivityItem",
                        "timestamp": 1_777_000_000_000,
                        "author": {"login": "fausto", "fullName": "Fausto Suarez Rosario"},
                        "category": {"id": "IssueCreatedCategory"},
                        "field": {"id": "created", "name": "created"},
                        "target": {"id": "78-1", "idReadable": "RAD-1055", "summary": "Investigate patching flow"},
                        "targetMember": None,
                        "added": [],
                        "removed": [],
                    }
                ],
                "afterCursor": None,
                "hasAfter": False,
            },
        )

    routes = {
        ("GET", "/api/issues/RAD-1055"): _issue_route,
        ("GET", "/api/issues/RAD-1055/attachments"): _attachments_route,
        ("GET", "/api/issues/RAD-1055/comments"): _comments_route,
        ("GET", "/api/issues/RAD-1055/links"): _links_route,
        ("GET", "/api/issues/RAD-1055/activitiesPage"): _activities_route,
    }

    monkeypatch.setenv("YOUTRACK_TOKEN", "test-token")
    with GitHubApiStubServer(routes=routes) as server:
        provider = _provider(base_url=server.base_url)
        payload = provider.get_ticket_by_id(work_item_id="RAD-1055")

    assert payload["provider"] == "youtrack"
    assert payload["id"] == "RAD-1055"
    assert payload["fields"] == {
        "System.WorkItemType": "Task",
        "System.State": "In Progress",
        "System.Title": "Investigate patching flow",
    }
    assert payload["metadata"]["Project"] == "Release and Deployments (RAD)"
    assert payload["metadata"]["Impact"] == "4"
    assert payload["description"] == "Ticket body\n![](image1.png)"
    assert [attachment["name"] for attachment in payload["attachments"]] == ["image1.png", "notes.txt"]
    assert payload["comments"][0]["text"] == "Looks good ![](comment-image.png)"
    assert payload["comments"][0]["attachments"][0]["url"] == f"{server.base_url}/api/files/4"
    assert payload["reactions"][0]["reaction"] == "thumbs-up"
    assert payload["links"][0]["issues"][0]["id"] == "RAD-1004"
    assert payload["timeline"][0]["action"] == "created issue"


def test_youtrack_search_work_items_returns_standard_shape(monkeypatch: Any) -> None:
    def _issues_route(request: StubRequest) -> StubResponse:
        assert request.query["query"] == ["patch flow State: {In Progress} Type: Task for: fausto"]
        assert request.query["$skip"] == ["2"]
        assert request.query["$top"] == ["3"]
        assert request.query["customFields"] == ["Type", "State", "Priority", "Assignee"]
        return StubResponse(
            status=200,
            body=[
                {
                    "idReadable": "RAD-1055",
                    "summary": "Investigate patching flow",
                    "created": 1_777_000_000_000,
                    "updated": 1_777_100_000_000,
                    "resolved": None,
                    "project": {"name": "Release and Deployments", "shortName": "RAD"},
                    "tags": [{"name": "patching"}],
                    "customFields": [
                        {"name": "Type", "value": {"name": "Task"}},
                        {"name": "State", "value": {"name": "In Progress"}},
                        {"name": "Assignee", "value": {"login": "fausto"}},
                    ],
                },
                {
                    "idReadable": "RAD-1056",
                    "summary": "Ship patch UX",
                    "created": 1_777_000_100_000,
                    "updated": 1_777_100_100_000,
                    "resolved": None,
                    "project": {"name": "Release and Deployments", "shortName": "RAD"},
                    "tags": [],
                    "customFields": [
                        {"name": "Type", "value": {"name": "Task"}},
                        {"name": "State", "value": {"name": "Open"}},
                    ],
                },
                {
                    "idReadable": "RAD-1057",
                    "summary": "Overflow item",
                    "created": 1_777_000_200_000,
                    "updated": 1_777_100_200_000,
                    "resolved": None,
                    "project": {"name": "Release and Deployments", "shortName": "RAD"},
                    "tags": [],
                    "customFields": [],
                },
            ],
        )

    monkeypatch.setenv("YOUTRACK_TOKEN", "test-token")
    with GitHubApiStubServer(routes={("GET", "/api/issues"): _issues_route}) as server:
        provider = _provider(base_url=server.base_url)
        payload = provider.search_work_items(
            query="patch flow",
            state="In Progress",
            work_item_type="Task",
            assigned_to="fausto",
            skip=2,
            take=2,
        )

    assert payload == {
        "provider": "youtrack",
        "matchesCount": 5,
        "matchesCountLowerBound": True,
        "returned_count": 2,
        "has_more": True,
        "results": [
            {
                "id": "RAD-1055",
                "title": "Investigate patching flow",
                "state": "In Progress",
                "type": "Task",
                "priority": None,
                "project": "RAD",
                "project_name": "Release and Deployments",
                "assigned_to": "fausto",
                "tags": ["patching"],
                "created_date": "2026-04-24T03:06:40Z",
                "changed_date": "2026-04-25T06:53:20Z",
                "url": f"{server.base_url}/issue/RAD-1055",
                "highlights": [],
            },
            {
                "id": "RAD-1056",
                "title": "Ship patch UX",
                "state": "Open",
                "type": "Task",
                "priority": None,
                "project": "RAD",
                "project_name": "Release and Deployments",
                "assigned_to": None,
                "tags": [],
                "created_date": "2026-04-24T03:08:20Z",
                "changed_date": "2026-04-25T06:55:00Z",
                "url": f"{server.base_url}/issue/RAD-1056",
                "highlights": [],
            },
        ],
    }


def test_youtrack_get_my_work_items_uses_me_query(monkeypatch: Any) -> None:
    captured_queries: list[str] = []

    def _issues_route(request: StubRequest) -> StubResponse:
        captured_queries.append(request.query["query"][0])
        return StubResponse(status=200, body=[])

    monkeypatch.setenv("YOUTRACK_TOKEN", "test-token")
    with GitHubApiStubServer(routes={("GET", "/api/issues"): _issues_route}) as server:
        provider = _provider(base_url=server.base_url)
        payload = provider.get_my_work_items(include_closed=False, skip=0, take=1)

    assert captured_queries == ["#Unresolved for: me"]
    assert payload == {
        "provider": "youtrack",
        "matchesCount": 0,
        "matchesCountLowerBound": False,
        "returned_count": 0,
        "has_more": False,
        "results": [],
        "warnings": [],
    }
