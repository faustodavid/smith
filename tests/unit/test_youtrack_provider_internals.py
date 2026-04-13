from __future__ import annotations

from typing import Any

import pytest
import requests
from tests.support import make_runtime_config

from smith.errors import SmithAuthError
from smith.providers.youtrack import YouTrackProvider


def _provider(
    *,
    api_url: str = "https://youtrack.example.test/api",
    config: Any | None = None,
    token_env: str | None = None,
) -> YouTrackProvider:
    return YouTrackProvider(
        config=config or make_runtime_config(),
        session=requests.Session(),
        youtrack_api_url=api_url,
        token_env=token_env,
    )


def test_youtrack_token_helpers_and_build_url(monkeypatch: Any) -> None:
    provider = _provider()
    monkeypatch.setenv("YOUTRACK_TOKEN", "test-token")

    assert provider._get_token() == "test-token"
    assert provider._get_token() == "test-token"
    assert provider._build_url("/issues") == "https://youtrack.example.test/api/issues"
    assert provider._issue_url("RAD-1055") == "https://youtrack.example.test/issue/RAD-1055"
    assert provider._auth_error_message() == (
        "YouTrack authentication rejected with HTTP 401/403. "
        "Set YOUTRACK_TOKEN and retry."
    )


def test_youtrack_token_requires_env(monkeypatch: Any) -> None:
    monkeypatch.delenv("YOUTRACK_TOKEN", raising=False)
    provider = _provider()

    with pytest.raises(SmithAuthError, match="Failed to acquire YouTrack token. Set YOUTRACK_TOKEN and retry."):
        provider._get_token()


def test_youtrack_token_requires_custom_env(monkeypatch: Any) -> None:
    monkeypatch.delenv("COMPANY_YOUTRACK_TOKEN", raising=False)
    provider = _provider(token_env="COMPANY_YOUTRACK_TOKEN")

    with pytest.raises(
        SmithAuthError,
        match="Failed to acquire YouTrack token. Set COMPANY_YOUTRACK_TOKEN and retry.",
    ):
        provider._get_token()

    assert provider._auth_error_message() == (
        "YouTrack authentication rejected with HTTP 401/403. "
        "Set COMPANY_YOUTRACK_TOKEN and retry."
    )


def test_youtrack_normalizes_custom_fields_and_comment_images() -> None:
    provider = _provider()

    custom_fields = provider._normalize_custom_fields(
        [
            {"name": "Priority", "value": {"name": "Normal"}, "$type": "SingleEnumIssueCustomField"},
            {"name": "Sponsor", "value": [{"fullName": "Alice"}, {"login": "bob"}], "$type": "MultiUserIssueCustomField"},
            {"name": "Spent time", "value": {"minutes": 90}, "$type": "PeriodIssueCustomField"},
            {"name": "Assignee", "value": None, "$type": "SingleUserIssueCustomField"},
        ]
    )
    comment = provider._normalize_comment(
        {
            "id": "102-1",
            "text": "See screenshot ![](image1.png)",
            "created": 1_777_000_000_000,
            "author": {"login": "fausto", "fullName": "Fausto Suarez Rosario"},
            "attachments": [
                {
                    "id": "104-1",
                    "name": "image1.png",
                    "mimeType": "image/png",
                    "url": "/api/files/1",
                    "thumbnailURL": "/api/files/2",
                }
            ],
            "reactions": [{"reaction": "thumbs-up", "author": {"login": "alice", "fullName": "Alice"}}],
        },
    )

    assert custom_fields == [
        {"id": None, "name": "Priority", "type": "SingleEnumIssueCustomField", "value": "Normal"},
        {"id": None, "name": "Sponsor", "type": "MultiUserIssueCustomField", "value": "Alice, bob"},
        {"id": None, "name": "Spent time", "type": "PeriodIssueCustomField", "value": "90m"},
        {"id": None, "name": "Assignee", "type": "SingleUserIssueCustomField", "value": None},
    ]
    assert comment is not None
    assert comment["text"] == "See screenshot ![](image1.png)"
    assert comment["attachments"][0]["name"] == "image1.png"
    assert comment["attachments"][0]["url"] == "https://youtrack.example.test/api/files/1"
    assert comment["reactions"][0]["reaction"] == "thumbs-up"


def test_youtrack_search_builds_query_and_reports_lower_bound_counts(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []

    def _fake_request(method: str, path: str, *, params: dict[str, Any] | None = None, expect_json: bool = True, **kwargs: Any) -> Any:
        del kwargs
        calls.append({"method": method, "path": path, "params": params, "expect_json": expect_json})
        return [
            {
                "idReadable": "RAD-1055",
                "summary": "Investigate patching flow",
                "created": 1_777_000_000_000,
                "updated": 1_777_100_000_000,
                "project": {"shortName": "RAD", "name": "Release and Deployments"},
                "customFields": [
                    {"name": "Type", "value": {"name": "Task"}},
                    {"name": "State", "value": {"name": "In Progress"}},
                    {"name": "Assignee", "value": {"login": "fausto"}},
                ],
                "tags": [{"name": "patching"}],
            },
            {
                "idReadable": "RAD-1056",
                "summary": "Ship patch UX",
                "created": 1_777_000_100_000,
                "updated": 1_777_100_100_000,
                "project": {"shortName": "RAD", "name": "Release and Deployments"},
                "customFields": [{"name": "Type", "value": {"name": "Task"}}, {"name": "State", "value": {"name": "Open"}}],
                "tags": [],
            },
            {
                "idReadable": "RAD-1057",
                "summary": "Overflow item",
                "project": {"shortName": "RAD", "name": "Release and Deployments"},
                "customFields": [],
                "tags": [],
            },
        ]

    monkeypatch.setattr(provider, "_request", _fake_request)

    result = provider.search_work_items(
        query="patch flow",
        state="In Progress",
        work_item_type="Task",
        assigned_to="fausto",
        skip=1,
        take=2,
    )

    assert calls == [
        {
            "method": "GET",
            "path": "/issues",
            "params": {
                "fields": (
                    "id,idReadable,summary,created,updated,resolved,"
                    "project(name,shortName),"
                    "tags(name),"
                    "customFields(name,value($type,localizedName,name,fullName,login,presentation,isResolved))"
                ),
                "$skip": 1,
                "$top": 3,
                "customFields": ["Type", "State", "Priority", "Assignee"],
                "query": "patch flow State: {In Progress} Type: Task for: fausto",
            },
            "expect_json": True,
        }
    ]
    assert result["returned_count"] == 2
    assert result["has_more"] is True
    assert result["matchesCount"] == 4
    assert result["matchesCountLowerBound"] is True
    assert result["results"][0]["id"] == "RAD-1055"
    assert result["results"][0]["project"] == "RAD"


def test_youtrack_get_my_work_items_delegates_to_search(monkeypatch: Any) -> None:
    provider = _provider()
    captured: dict[str, Any] = {}

    def _fake_search_work_items(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"matchesCount": 3, "matchesCountLowerBound": False, "returned_count": 2, "has_more": True, "results": ["a", "b"]}

    monkeypatch.setattr(provider, "search_work_items", _fake_search_work_items)

    result = provider.get_my_work_items(include_closed=False, skip=2, take=5)

    assert captured == {
        "query": "",
        "project": None,
        "repo": None,
        "assigned_to": "@me",
        "skip": 2,
        "take": 5,
        "include_closed": False,
    }
    assert result == {
        "provider": "youtrack",
        "matchesCount": 3,
        "matchesCountLowerBound": False,
        "returned_count": 2,
        "has_more": True,
        "results": ["a", "b"],
        "warnings": [],
    }
