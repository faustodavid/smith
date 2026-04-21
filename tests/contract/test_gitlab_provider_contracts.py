from __future__ import annotations

import json
import os
import time
from typing import Any

import pytest
import requests
from tests.support import make_runtime_config

from smith.discovery import DiscoveryQuery
from smith.errors import SmithApiError
from smith.pipeline_listing import PipelineListQuery
from smith.providers.gitlab import GitLabProvider

_FULL_REPO = "gitlab-org/repo-a"
_FULL_GROUP = "gitlab-org"


def _provider(config: Any | None = None, *, gitlab_org: str | None = _FULL_GROUP) -> GitLabProvider:
    return GitLabProvider(
        config=config or make_runtime_config(),
        session=requests.Session(),
        gitlab_org=gitlab_org,
    )


def _cache_git_output(
    provider: GitLabProvider,
    *,
    tracked_paths: list[str] | None = None,
    repo: str = _FULL_REPO,
    origin_url: str | None = None,
) -> Any:
    expected_origin = origin_url or provider._local_checkout_remote_url(repo=repo)
    tracked_output = "\0".join(tracked_paths or [])
    if tracked_output:
        tracked_output += "\0"

    def _fake_git_output(args: list[str], **kwargs: Any) -> str:
        if "remote" in args:
            return f"{expected_origin}\n"
        if "ls-files" in args:
            return tracked_output
        raise AssertionError(f"unexpected git output command: {args}")

    return _fake_git_output


class _FakeJsonResponse:
    def __init__(
        self,
        payload: Any,
        *,
        headers: dict[str, str] | None = None,
        status_code: int = 200,
    ) -> None:
        self._payload = payload
        self.headers = headers or {}
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> Any:
        return self._payload


def test_gitlab_maps_group_repository_views_and_search_code(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []
    list_calls: list[dict[str, Any]] = []

    def _fake_paginated_list(path: str, **kwargs: Any) -> list[dict[str, Any]]:
        list_calls.append({"path": path, **kwargs})
        if path == "/groups":
            return [
                {
                    "id": "gitlab-org",
                    "full_path": "gitlab-org",
                    "web_url": "https://gitlab.com/gitlab-org",
                }
            ]
        return [
            {
                "id": 1,
                "path_with_namespace": _FULL_REPO,
                "default_branch": "main",
                "web_url": "https://gitlab.com/gitlab-org/repo-a",
            }
        ]

    monkeypatch.setattr(provider, "_get_paginated_list", _fake_paginated_list)

    def _fake_request_response(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        calls.append({"method": method, "path": path, "params": params})
        return _FakeJsonResponse(
            [
                {"path": "src/app.py"},
                {"path": "src/util.py"},
            ],
            headers={"X-Total": "2"},
        )

    monkeypatch.setattr(provider, "_request_response", _fake_request_response)

    assert provider.list_projects() == [
        {
            "id": "gitlab-org",
            "name": "gitlab-org",
            "state": "active",
            "url": "https://gitlab.com/gitlab-org",
        }
    ]
    assert provider.list_repositories() == [
        {
            "id": 1,
            "name": _FULL_REPO,
            "defaultBranch": "main",
            "webUrl": "https://gitlab.com/gitlab-org/repo-a",
        }
    ]
    assert list_calls == [
        {
            "path": "/groups",
            "params": {"all_available": "false", "order_by": "path"},
        },
        {
            "path": "/projects",
            "params": {"membership": "true", "simple": "true", "order_by": "path"},
        },
    ]

    result = provider.search_code(query="grafana", project=None, repos=[_FULL_REPO], skip=1, take=1)

    assert result == {"matchesCount": 2, "results": [f"{_FULL_REPO}:/src/util.py"]}
    assert calls == [
        {
            "method": "GET",
            "path": "/projects/gitlab-org%2Frepo-a/search",
            "params": {"scope": "blobs", "search": "grafana", "per_page": 100, "page": 1},
        }
    ]


def test_gitlab_discover_groups_applies_grep_skip_take_and_truncation(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []

    def _fake_paginated_page(
        path: str,
        *,
        params: dict[str, Any] | None,
        page: int,
        per_page: int,
    ) -> tuple[list[dict[str, Any]], int | None]:
        calls.append({"path": path, "params": params, "page": page, "per_page": per_page})
        return [
            {"id": "1", "full_path": "infra", "web_url": "https://gitlab.com/infra"},
            {"id": "2", "full_path": "platform/api", "web_url": "https://gitlab.com/platform/api"},
            {"id": "3", "full_path": "platform/web", "web_url": "https://gitlab.com/platform/web"},
            {"id": "4", "full_path": "platform/cli", "web_url": "https://gitlab.com/platform/cli"},
        ], 1

    monkeypatch.setattr(provider, "_get_paginated_page", _fake_paginated_page)

    result = provider.discover_groups(query=DiscoveryQuery.create(grep="^platform", skip=1, take=1))

    assert result == {
        "results": [
            {
                "id": "3",
                "name": "platform/web",
                "state": "active",
                "url": "https://gitlab.com/platform/web",
            }
        ],
        "returned_count": 1,
        "has_more": True,
        "warnings": ["showing 1 matching groups; use --skip/--take to see more."],
        "partial": True,
    }
    assert calls == [
        {
            "path": "/groups",
            "params": {"all_available": "false", "order_by": "path"},
            "page": 1,
            "per_page": 100,
        }
    ]


def test_gitlab_discover_repositories_scopes_group_and_truncates(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []

    def _fake_paginated_page(
        path: str,
        *,
        params: dict[str, Any] | None,
        page: int,
        per_page: int,
    ) -> tuple[list[dict[str, Any]], int | None]:
        calls.append({"path": path, "params": params, "page": page, "per_page": per_page})
        return [
            {
                "id": 1,
                "path_with_namespace": "engineering-tools/platform/api",
                "default_branch": "main",
                "web_url": "https://gitlab.com/engineering-tools/platform/api",
            },
            {
                "id": 2,
                "path_with_namespace": "engineering-tools/platform/web",
                "default_branch": "main",
                "web_url": "https://gitlab.com/engineering-tools/platform/web",
            },
            {
                "id": 3,
                "path_with_namespace": "engineering-tools/platform/cli",
                "default_branch": "main",
                "web_url": "https://gitlab.com/engineering-tools/platform/cli",
            },
        ], 1

    monkeypatch.setattr(provider, "_get_paginated_page", _fake_paginated_page)

    result = provider.discover_repositories(
        group="engineering-tools/platform",
        query=DiscoveryQuery.create(skip=1, take=1),
    )

    assert result == {
        "results": [
            {
                "id": 2,
                "name": "engineering-tools/platform/web",
                "defaultBranch": "main",
                "webUrl": "https://gitlab.com/engineering-tools/platform/web",
            }
        ],
        "returned_count": 1,
        "has_more": True,
        "warnings": ["showing 1 matching repositories; use --skip/--take to see more."],
        "partial": True,
    }
    assert calls == [
        {
            "path": "/groups/engineering-tools%2Fplatform/projects",
            "params": {
                "include_subgroups": "true",
                "simple": "true",
                "order_by": "path",
            },
            "page": 1,
            "per_page": 100,
        }
    ]


def test_gitlab_discover_groups_does_not_poison_cache_when_filtered(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []

    def _fake_paginated_page(
        path: str,
        *,
        params: dict[str, Any] | None,
        page: int,
        per_page: int,
    ) -> tuple[list[dict[str, Any]], int | None]:
        calls.append({"kind": "page", "path": path, "params": params, "page": page, "per_page": per_page})
        return [
            {"id": "1", "full_path": "infra", "web_url": "https://gitlab.com/infra"},
            {"id": "2", "full_path": "platform/api", "web_url": "https://gitlab.com/platform/api"},
        ], 1

    monkeypatch.setattr(provider, "_get_paginated_page", _fake_paginated_page)

    result = provider.discover_groups(query=DiscoveryQuery.create(grep="platform"))
    listed = provider.list_groups()

    assert result["results"] == [
        {
            "id": "2",
            "name": "platform/api",
            "state": "active",
            "url": "https://gitlab.com/platform/api",
        }
    ]
    assert listed == [
        {
            "id": "1",
            "name": "infra",
            "state": "active",
            "url": "https://gitlab.com/infra",
        },
        {
            "id": "2",
            "name": "platform/api",
            "state": "active",
            "url": "https://gitlab.com/platform/api",
        },
    ]
    assert calls == [
        {
            "kind": "page",
            "path": "/groups",
            "params": {"all_available": "false", "order_by": "path"},
            "page": 1,
            "per_page": 100,
        },
    ]


def test_gitlab_discover_groups_skips_server_search_for_nested_paths(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []

    def _fake_paginated_page(
        path: str,
        *,
        params: dict[str, Any] | None,
        page: int,
        per_page: int,
    ) -> tuple[list[dict[str, Any]], int | None]:
        calls.append({"path": path, "params": params, "page": page, "per_page": per_page})
        return [
            {"id": "2", "full_path": "platform/api", "web_url": "https://gitlab.com/platform/api"},
        ], 1

    monkeypatch.setattr(provider, "_get_paginated_page", _fake_paginated_page)

    result = provider.discover_groups(query=DiscoveryQuery.create(grep="platform/api"))

    assert result["results"] == [
        {
            "id": "2",
            "name": "platform/api",
            "state": "active",
            "url": "https://gitlab.com/platform/api",
        }
    ]
    assert calls == [
        {
            "path": "/groups",
            "params": {"all_available": "false", "order_by": "path"},
            "page": 1,
            "per_page": 100,
        }
    ]


def test_gitlab_discover_repositories_uses_server_search_for_simple_substrings(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []

    def _fake_paginated_page(
        path: str,
        *,
        params: dict[str, Any] | None,
        page: int,
        per_page: int,
    ) -> tuple[list[dict[str, Any]], int | None]:
        calls.append({"path": path, "params": params, "page": page, "per_page": per_page})
        return [
            {
                "id": 1,
                "path_with_namespace": "engineering-tools/platform/api",
                "default_branch": "main",
                "web_url": "https://gitlab.com/engineering-tools/platform/api",
            }
        ], 1

    monkeypatch.setattr(provider, "_get_paginated_page", _fake_paginated_page)

    result = provider.discover_repositories(query=DiscoveryQuery.create(grep="api"))

    assert result["results"] == [
        {
            "id": 1,
            "name": "engineering-tools/platform/api",
            "defaultBranch": "main",
            "webUrl": "https://gitlab.com/engineering-tools/platform/api",
        }
    ]
    assert calls == [
        {
            "path": "/projects",
            "params": {
                "membership": "true",
                "simple": "true",
                "order_by": "path",
                "search": "api",
                "search_namespaces": "true",
            },
            "page": 1,
            "per_page": 100,
        }
    ]


def test_gitlab_discover_repositories_skips_server_search_for_namespace_queries(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []

    def _fake_paginated_page(
        path: str,
        *,
        params: dict[str, Any] | None,
        page: int,
        per_page: int,
    ) -> tuple[list[dict[str, Any]], int | None]:
        calls.append({"path": path, "params": params, "page": page, "per_page": per_page})
        return [
            {
                "id": 1,
                "path_with_namespace": "engineering-tools/platform/api",
                "default_branch": "main",
                "web_url": "https://gitlab.com/engineering-tools/platform/api",
            }
        ], 1

    monkeypatch.setattr(provider, "_get_paginated_page", _fake_paginated_page)

    result = provider.discover_repositories(
        group="engineering-tools",
        query=DiscoveryQuery.create(grep="platform"),
    )

    assert result["results"] == [
        {
            "id": 1,
            "name": "engineering-tools/platform/api",
            "defaultBranch": "main",
            "webUrl": "https://gitlab.com/engineering-tools/platform/api",
        }
    ]
    assert calls == [
        {
            "path": "/groups/engineering-tools/projects",
            "params": {
                "include_subgroups": "true",
                "simple": "true",
                "order_by": "path",
            },
            "page": 1,
            "per_page": 100,
        }
    ]


def test_gitlab_discover_repositories_enables_namespace_search_for_unscoped_search(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []

    def _fake_paginated_page(
        path: str,
        *,
        params: dict[str, Any] | None,
        page: int,
        per_page: int,
    ) -> tuple[list[dict[str, Any]], int | None]:
        calls.append({"path": path, "params": params, "page": page, "per_page": per_page})
        return [
            {
                "id": 1,
                "path_with_namespace": "engineering-tools/platform/api",
                "default_branch": "main",
                "web_url": "https://gitlab.com/engineering-tools/platform/api",
            }
        ], 1

    monkeypatch.setattr(provider, "_get_paginated_page", _fake_paginated_page)

    result = provider.discover_repositories(query=DiscoveryQuery.create(grep="platform"))

    assert result["results"] == [
        {
            "id": 1,
            "name": "engineering-tools/platform/api",
            "defaultBranch": "main",
            "webUrl": "https://gitlab.com/engineering-tools/platform/api",
        }
    ]
    assert calls == [
        {
            "path": "/projects",
            "params": {
                "membership": "true",
                "simple": "true",
                "order_by": "path",
                "search": "platform",
                "search_namespaces": "true",
            },
            "page": 1,
            "per_page": 100,
        }
    ]


def test_gitlab_discover_repositories_falls_back_when_server_search_errors(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []

    def _fake_paginated_page(
        path: str,
        *,
        params: dict[str, Any] | None,
        page: int,
        per_page: int,
    ) -> tuple[list[dict[str, Any]], int | None]:
        calls.append({"path": path, "params": params, "page": page, "per_page": per_page})
        if params and params.get("search"):
            raise SmithApiError("server search unavailable")
        return [
            {
                "id": 1,
                "path_with_namespace": "engineering-tools/platform/api",
                "default_branch": "main",
                "web_url": "https://gitlab.com/engineering-tools/platform/api",
            }
        ], 1

    monkeypatch.setattr(provider, "_get_paginated_page", _fake_paginated_page)

    result = provider.discover_repositories(query=DiscoveryQuery.create(grep="platform"))

    assert result["results"] == [
        {
            "id": 1,
            "name": "engineering-tools/platform/api",
            "defaultBranch": "main",
            "webUrl": "https://gitlab.com/engineering-tools/platform/api",
        }
    ]
    assert calls == [
        {
            "path": "/projects",
            "params": {
                "membership": "true",
                "simple": "true",
                "order_by": "path",
                "search": "platform",
                "search_namespaces": "true",
            },
            "page": 1,
            "per_page": 100,
        },
        {
            "path": "/projects",
            "params": {
                "membership": "true",
                "simple": "true",
                "order_by": "path",
            },
            "page": 1,
            "per_page": 100,
        },
    ]


def test_gitlab_discover_groups_stops_after_required_window(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[int] = []
    first_page = [
        {
            "id": str(index),
            "full_path": f"platform/group-{index:03d}",
            "web_url": f"https://gitlab.com/platform/group-{index:03d}",
        }
        for index in range(100)
    ]

    def _fake_paginated_page(
        path: str,
        *,
        params: dict[str, Any] | None,
        page: int,
        per_page: int,
    ) -> tuple[list[dict[str, Any]], int | None]:
        assert path == "/groups"
        assert params == {"all_available": "false", "order_by": "path"}
        assert per_page == 100
        calls.append(page)
        if page != 1:
            raise AssertionError("did not expect discovery to fetch additional pages")
        return first_page, 3

    monkeypatch.setattr(provider, "_get_paginated_page", _fake_paginated_page)

    result = provider.discover_groups(query=DiscoveryQuery.create())

    assert result["returned_count"] == 50
    assert result["has_more"] is True
    assert calls == [1]


def test_gitlab_paginated_list_preserves_page_order_when_fetching_in_parallel(monkeypatch: Any) -> None:
    provider = _provider()

    def _fake_request_response(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        del method, path, kwargs
        page = int((params or {}).get("page", 1))
        if page == 2:
            time.sleep(0.05)
        elif page == 3:
            time.sleep(0.01)
        return _FakeJsonResponse(
            [{"page": page}],
            headers={"X-Total-Pages": "3"} if page == 1 else {},
        )

    monkeypatch.setattr(provider, "_request_response", _fake_request_response)

    result = provider._get_paginated_list("/groups", params={"all_available": "false", "order_by": "path"})

    assert result == [{"page": 1}, {"page": 2}, {"page": 3}]


def test_gitlab_paginated_list_uses_stable_page_size_when_limit_is_applied(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []

    def _fake_request_response(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        del method, path, kwargs
        calls.append(dict(params or {}))
        page = int((params or {}).get("page", 1))
        per_page = int((params or {}).get("per_page", 100))
        start = (page - 1) * per_page
        payload = [{"index": start + offset} for offset in range(per_page)]
        return _FakeJsonResponse(payload)

    monkeypatch.setattr(provider, "_request_response", _fake_request_response)

    result = provider._get_paginated_list("/groups", params={"order_by": "path"}, limit=150)

    assert len(result) == 150
    assert result[0] == {"index": 0}
    assert result[-1] == {"index": 149}
    assert calls == [
        {"order_by": "path", "per_page": 100, "page": 1},
        {"order_by": "path", "per_page": 100, "page": 2},
    ]


def test_gitlab_search_code_uses_pagination_headers_for_exact_total(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []

    def _fake_request_response(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        calls.append({"method": method, "path": path, "params": params})
        page = int((params or {}).get("page", 1))
        if page != 1:
            raise AssertionError("did not expect additional pages when the first page already covers the requested window")
        return _FakeJsonResponse(
            [{"path": "src/app.py"}],
            headers={"X-Total": "25", "X-Next-Page": "2"},
        )

    monkeypatch.setattr(provider, "_request_response", _fake_request_response)

    result = provider.search_code(query="grafana", project=None, repos=[_FULL_REPO], skip=0, take=1)

    assert result == {"matchesCount": 25, "results": [f"{_FULL_REPO}:/src/app.py"]}
    assert calls == [
        {
            "method": "GET",
            "path": "/projects/gitlab-org%2Frepo-a/search",
            "params": {"scope": "blobs", "search": "grafana", "per_page": 100, "page": 1},
        }
    ]


def test_gitlab_search_code_falls_back_to_complete_pagination_for_exact_total(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []
    page_one = [{"path": f"src/file-{index:03d}.py"} for index in range(100)]
    page_two = [{"path": f"src/file-{100 + index:03d}.py"} for index in range(5)]

    def _fake_request_response(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        calls.append({"method": method, "path": path, "params": params})
        page = int((params or {}).get("page", 1))
        if page == 1:
            return _FakeJsonResponse(page_one)
        if page == 2:
            return _FakeJsonResponse(page_two)
        if page == 3:
            return _FakeJsonResponse([])
        raise AssertionError(f"unexpected page request: {page}")

    monkeypatch.setattr(provider, "_request_response", _fake_request_response)

    result = provider.search_code(query="grafana", project=None, repos=[_FULL_REPO], skip=100, take=2)

    assert result == {
        "matchesCount": 105,
        "results": [
            f"{_FULL_REPO}:/src/file-100.py",
            f"{_FULL_REPO}:/src/file-101.py",
        ],
    }
    assert calls == [
        {
            "method": "GET",
            "path": "/projects/gitlab-org%2Frepo-a/search",
            "params": {"scope": "blobs", "search": "grafana", "per_page": 100, "page": 1},
        },
        {
            "method": "GET",
            "path": "/projects/gitlab-org%2Frepo-a/search",
            "params": {"scope": "blobs", "search": "grafana", "per_page": 100, "page": 2},
        },
        {
            "method": "GET",
            "path": "/projects/gitlab-org%2Frepo-a/search",
            "params": {"scope": "blobs", "search": "grafana", "per_page": 100, "page": 3},
        },
    ]


def test_gitlab_search_code_uses_group_scoped_search_for_broad_queries(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []
    page_one = [{"path": f"src/file-{index:03d}.py", "path_with_namespace": _FULL_REPO} for index in range(100)]

    def _fake_request_response(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        calls.append({"method": method, "path": path, "params": params})
        page = int((params or {}).get("page", 1))
        if page == 1:
            return _FakeJsonResponse(page_one, headers={"X-Total": "200", "X-Next-Page": "2"})
        raise AssertionError(f"did not expect additional pages when the first page already covers the requested window: {page}")

    monkeypatch.setattr(provider, "_request_response", _fake_request_response)

    result = provider.search_code(query="grafana", project=None, repos=None, skip=0, take=20)

    assert result == {
        "matchesCount": 200,
        "results": [f"{_FULL_REPO}:/src/file-{index:03d}.py" for index in range(20)],
    }
    assert calls == [
        {
            "method": "GET",
            "path": "/groups/gitlab-org/search",
            "params": {"scope": "blobs", "search": "grafana", "per_page": 100, "page": 1},
        },
    ]


def test_gitlab_search_code_global_fallback_keeps_lower_bound_warning_without_group(monkeypatch: Any) -> None:
    provider = _provider(gitlab_org=None)
    calls: list[dict[str, Any]] = []
    page_one = [{"path": f"src/file-{index:03d}.py", "path_with_namespace": _FULL_REPO} for index in range(100)]
    page_two = [{"path": f"src/file-{100 + index:03d}.py", "path_with_namespace": _FULL_REPO} for index in range(100)]

    def _fake_request_response(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        calls.append({"method": method, "path": path, "params": params})
        page = int((params or {}).get("page", 1))
        if page == 1:
            return _FakeJsonResponse(page_one)
        if page == 2:
            return _FakeJsonResponse(page_two)
        raise AssertionError(f"unexpected page request: {page}")

    monkeypatch.setattr(provider, "_request_response", _fake_request_response)

    result = provider.search_code(query="grafana", project=None, repos=None, skip=0, take=20)

    assert result == {
        "matchesCount": 200,
        "matchesCountLowerBound": True,
        "results": [f"{_FULL_REPO}:/src/file-{index:03d}.py" for index in range(20)],
        "warnings": [
            "GitLab search did not provide an exact total; `matchesCount` is a lower bound. "
            "Narrow with `--repo group/project` for exact counts."
        ],
        "partial": True,
    }
    assert calls == [
        {
            "method": "GET",
            "path": "/search",
            "params": {"scope": "blobs", "search": "grafana", "per_page": 100, "page": 1},
        },
        {
            "method": "GET",
            "path": "/search",
            "params": {"scope": "blobs", "search": "grafana", "per_page": 100, "page": 2},
        },
    ]


def test_gitlab_grep_files_with_matches_uses_server_side_search_api(monkeypatch: Any) -> None:
    provider = _provider(make_runtime_config(max_output_chars=50))
    calls: list[dict[str, Any]] = []

    def _fake_request(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        calls.append({"method": method, "path": path, "params": params})
        return [
            {"path": "src/app.py"},
            {"path": "src/postgres.py"},
            {"path": "src/lib/util.py"},
            {"path": "docs/postgres.md"},
        ]

    def _unexpected_repository_walk(**kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError("expected Search API fast path")

    monkeypatch.setattr(provider, "_request", _fake_request)
    monkeypatch.setattr(provider, "_get_repository_files", _unexpected_repository_walk)
    monkeypatch.setattr(
        provider,
        "_get_file_text",
        lambda *, file_path, **kwargs: "postgres\nok" if file_path in {"/src/app.py", "/src/lib/util.py"} else "ok",
    )

    result = provider.grep(
        repo=_FULL_REPO,
        pattern="postgres",
        path="src",
        branch="feature",
        glob="*.py",
        output_mode="files_with_matches",
    )

    assert result == {
        "text": "/src/app.py\n/src/lib/util.py",
        "files_matched": 2,
        "warnings": [],
        "partial": False,
    }
    assert calls == [
        {
            "method": "GET",
            "path": "/projects/gitlab-org%2Frepo-a/search",
            "params": {"scope": "blobs", "search": "postgres", "per_page": 100, "ref": "feature", "page": 1},
        }
    ]


def test_gitlab_grep_files_with_matches_falls_back_when_search_api_errors(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(provider, "_get_project_default_branch", lambda repo: "main")

    def _failing_search(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        calls.append({"method": method, "path": path, "params": params})
        raise SmithApiError("search unavailable", status_code=400)

    monkeypatch.setattr(provider, "_request", _failing_search)
    monkeypatch.setattr(
        provider,
        "_get_repository_files",
        lambda **kwargs: [
            {"path": "/src/app.py", "is_binary": False, "sha": "sha-app"},
            {"path": "/src/util.py", "is_binary": False, "sha": "sha-util"},
        ],
    )
    monkeypatch.setattr(
        provider,
        "_get_file_text",
        lambda *, file_path, **kwargs: "postgres\nok" if file_path == "/src/app.py" else "ok",
    )

    result = provider.grep(repo=_FULL_REPO, pattern="postgres", output_mode="files_with_matches")

    assert result == {
        "text": "/src/app.py",
        "files_matched": 1,
        "warnings": [],
        "partial": False,
    }
    assert calls == [
        {
            "method": "GET",
            "path": "/projects/gitlab-org%2Frepo-a/search",
            "params": {"scope": "blobs", "search": "postgres", "per_page": 100, "page": 1},
        }
    ]


def test_gitlab_grep_uses_local_checkout_cache_when_available(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    monkeypatch.setenv("GITLAB_GREP_USE_LOCAL_CACHE", "true")
    monkeypatch.setenv("SMITH_GITLAB_GREP_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(provider, "_get_project_default_branch", lambda repo: "main")
    monkeypatch.setattr(provider, "_git_subprocess", lambda *args, **kwargs: None)
    monkeypatch.setattr(provider, "_git_subprocess_output", _cache_git_output(provider, tracked_paths=["src/app.py"]))

    checkout_dir = provider._local_checkout_path(repo=_FULL_REPO, branch="main")
    os.makedirs(checkout_dir, exist_ok=True)
    os.makedirs(os.path.join(checkout_dir, ".git"), exist_ok=True)
    os.makedirs(os.path.join(checkout_dir, "src"), exist_ok=True)
    with open(os.path.join(checkout_dir, ".git", "smith-last-fetch"), "w", encoding="utf-8"):
        pass
    with open(os.path.join(checkout_dir, "src", "app.py"), "w", encoding="utf-8") as file_handle:
        file_handle.write("ok\nerror\nerror\n")

    monkeypatch.setattr(
        provider,
        "_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not call GitLab API")),
    )

    result = provider.grep(repo=_FULL_REPO, pattern="error", output_mode="count")

    assert result == {
        "text": "/src/app.py:2",
        "files_matched": 1,
        "warnings": [],
        "partial": False,
    }


def test_gitlab_grep_reuses_fresh_cache_without_touching_fetch_marker(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    commands: list[list[str]] = []
    monkeypatch.setenv("GITLAB_GREP_USE_LOCAL_CACHE", "true")
    monkeypatch.setenv("SMITH_GITLAB_GREP_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(provider, "_get_project_default_branch", lambda repo: "main")
    monkeypatch.setattr(provider, "_git_subprocess", lambda args, **kwargs: commands.append(args))
    monkeypatch.setattr(provider, "_git_subprocess_output", _cache_git_output(provider, tracked_paths=["src/app.py"]))

    checkout_dir = provider._local_checkout_path(repo=_FULL_REPO, branch="main")
    marker_path = os.path.join(checkout_dir, ".git", "smith-last-fetch")
    os.makedirs(os.path.join(checkout_dir, ".git"), exist_ok=True)
    os.makedirs(os.path.join(checkout_dir, "src"), exist_ok=True)
    with open(marker_path, "w", encoding="utf-8"):
        pass
    with open(os.path.join(checkout_dir, "src", "app.py"), "w", encoding="utf-8") as file_handle:
        file_handle.write("error\n")
    marker_mtime = os.path.getmtime(marker_path)

    result = provider.grep(repo=_FULL_REPO, pattern="error", output_mode="count")

    assert result == {
        "text": "/src/app.py:1",
        "files_matched": 1,
        "warnings": [],
        "partial": False,
    }
    assert os.path.getmtime(marker_path) == marker_mtime
    assert commands == []


def test_gitlab_grep_falls_back_to_api_when_local_checkout_unavailable(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    monkeypatch.setenv("GITLAB_GREP_USE_LOCAL_CACHE", "true")
    monkeypatch.setenv("SMITH_GITLAB_GREP_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(provider, "_get_project_default_branch", lambda repo: "main")
    monkeypatch.setattr(provider, "_grep_via_search_api", lambda **kwargs: None)
    monkeypatch.setattr(
        provider,
        "_git_subprocess",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("git unavailable")),
    )
    monkeypatch.setattr(
        provider,
        "_get_repository_files",
        lambda **kwargs: [{"path": "/src/app.py", "is_binary": False, "sha": "sha-app"}],
    )
    monkeypatch.setattr(
        provider,
        "_get_file_text",
        lambda **kwargs: "error\nok\n",
    )

    result = provider.grep(repo=_FULL_REPO, pattern="error", output_mode="count")

    assert result == {
        "text": "/src/app.py:1",
        "files_matched": 1,
        "warnings": [],
        "partial": False,
    }


def test_gitlab_grep_falls_back_to_api_when_cache_origin_mismatches(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    monkeypatch.setenv("GITLAB_GREP_USE_LOCAL_CACHE", "true")
    monkeypatch.setenv("SMITH_GITLAB_GREP_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(provider, "_get_project_default_branch", lambda repo: "main")
    monkeypatch.setattr(provider, "_grep_via_search_api", lambda **kwargs: None)

    checkout_dir = provider._local_checkout_path(repo=_FULL_REPO, branch="main")
    os.makedirs(os.path.join(checkout_dir, ".git"), exist_ok=True)
    with open(os.path.join(checkout_dir, ".git", "smith-last-fetch"), "w", encoding="utf-8"):
        pass

    def _fake_git_output(args: list[str], **kwargs: Any) -> str:
        if "remote" in args:
            return "https://gitlab.com/other-group/repo-a.git\n"
        raise AssertionError("unexpected git output command")

    monkeypatch.setattr(provider, "_git_subprocess_output", _fake_git_output)
    monkeypatch.setattr(
        provider,
        "_get_repository_files",
        lambda **kwargs: [{"path": "/src/app.py", "is_binary": False, "sha": "sha-app"}],
    )
    monkeypatch.setattr(
        provider,
        "_get_file_text",
        lambda **kwargs: "error\nok\n",
    )

    result = provider.grep(repo=_FULL_REPO, pattern="error", output_mode="count")

    assert result == {
        "text": "/src/app.py:1",
        "files_matched": 1,
        "warnings": [],
        "partial": False,
    }


def test_gitlab_local_checkout_path_avoids_branch_collisions() -> None:
    provider = _provider()

    first = provider._local_checkout_path(repo=_FULL_REPO, branch="feature/a")
    second = provider._local_checkout_path(repo=_FULL_REPO, branch="feature_a")

    assert first != second


def test_gitlab_grep_local_checkout_rejects_path_traversal(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    monkeypatch.setenv("GITLAB_GREP_USE_LOCAL_CACHE", "true")
    monkeypatch.setenv("SMITH_GITLAB_GREP_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(provider, "_get_project_default_branch", lambda repo: "main")
    monkeypatch.setattr(provider, "_git_subprocess", lambda *args, **kwargs: None)
    monkeypatch.setattr(provider, "_git_subprocess_output", _cache_git_output(provider))

    checkout_dir = provider._local_checkout_path(repo=_FULL_REPO, branch="main")
    os.makedirs(checkout_dir, exist_ok=True)
    os.makedirs(os.path.join(checkout_dir, ".git"), exist_ok=True)
    with open(os.path.join(checkout_dir, ".git", "smith-last-fetch"), "w", encoding="utf-8"):
        pass

    secret_dir = tmp_path / "secret-area"
    secret_dir.mkdir()
    (secret_dir / "secret.txt").write_text("error\n", encoding="utf-8")

    result = provider.grep(repo=_FULL_REPO, pattern="error", path="../../secret-area", output_mode="count")

    assert result == {
        "text": "",
        "files_matched": 0,
        "warnings": [],
        "partial": False,
    }


def test_gitlab_grep_local_checkout_skips_symlinks(monkeypatch: Any, tmp_path: Any) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unsupported")

    provider = _provider()
    monkeypatch.setenv("GITLAB_GREP_USE_LOCAL_CACHE", "true")
    monkeypatch.setenv("SMITH_GITLAB_GREP_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(provider, "_get_project_default_branch", lambda repo: "main")
    monkeypatch.setattr(provider, "_git_subprocess", lambda *args, **kwargs: None)
    monkeypatch.setattr(provider, "_git_subprocess_output", _cache_git_output(provider, tracked_paths=["src/link.txt"]))

    checkout_dir = provider._local_checkout_path(repo=_FULL_REPO, branch="main")
    os.makedirs(checkout_dir, exist_ok=True)
    os.makedirs(os.path.join(checkout_dir, ".git"), exist_ok=True)
    os.makedirs(os.path.join(checkout_dir, "src"), exist_ok=True)
    with open(os.path.join(checkout_dir, ".git", "smith-last-fetch"), "w", encoding="utf-8"):
        pass

    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("error\n", encoding="utf-8")
    link_path = os.path.join(checkout_dir, "src", "link.txt")

    try:
        os.symlink(secret_file, link_path)
    except OSError as exc:
        pytest.skip(f"symlink setup unavailable: {exc}")

    result = provider.grep(repo=_FULL_REPO, pattern="error", output_mode="count")

    assert result == {
        "text": "",
        "files_matched": 0,
        "warnings": [],
        "partial": False,
    }


def test_gitlab_grep_local_checkout_skips_cache_marker_for_match_all(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    monkeypatch.setenv("GITLAB_GREP_USE_LOCAL_CACHE", "true")
    monkeypatch.setenv("SMITH_GITLAB_GREP_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(provider, "_get_project_default_branch", lambda repo: "main")
    monkeypatch.setattr(provider, "_git_subprocess", lambda *args, **kwargs: None)
    monkeypatch.setattr(provider, "_git_subprocess_output", _cache_git_output(provider, tracked_paths=["src/app.py"]))

    checkout_dir = provider._local_checkout_path(repo=_FULL_REPO, branch="main")
    os.makedirs(checkout_dir, exist_ok=True)
    os.makedirs(os.path.join(checkout_dir, ".git"), exist_ok=True)
    os.makedirs(os.path.join(checkout_dir, "src"), exist_ok=True)
    with open(os.path.join(checkout_dir, ".git", "smith-last-fetch"), "w", encoding="utf-8"):
        pass
    with open(os.path.join(checkout_dir, "src", "app.py"), "w", encoding="utf-8") as file_handle:
        file_handle.write("ok\n")

    result = provider.grep(repo=_FULL_REPO, pattern=".*", output_mode="files_with_matches")

    assert result == {
        "text": "/src/app.py",
        "files_matched": 1,
        "warnings": [],
        "partial": False,
    }


def test_gitlab_grep_local_checkout_rejects_internal_metadata_paths(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    monkeypatch.setenv("GITLAB_GREP_USE_LOCAL_CACHE", "true")
    monkeypatch.setenv("SMITH_GITLAB_GREP_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(provider, "_get_project_default_branch", lambda repo: "main")
    monkeypatch.setattr(provider, "_git_subprocess", lambda *args, **kwargs: None)
    monkeypatch.setattr(provider, "_git_subprocess_output", _cache_git_output(provider))

    checkout_dir = provider._local_checkout_path(repo=_FULL_REPO, branch="main")
    os.makedirs(os.path.join(checkout_dir, ".git"), exist_ok=True)
    with open(os.path.join(checkout_dir, ".git", "config"), "w", encoding="utf-8") as file_handle:
        file_handle.write("origin = secret\n")
    with open(os.path.join(checkout_dir, ".git", "smith-last-fetch"), "w", encoding="utf-8"):
        pass

    marker_result = provider.grep(
        repo=_FULL_REPO,
        pattern=".*",
        path=".git/smith-last-fetch",
        output_mode="files_with_matches",
    )
    git_result = provider.grep(repo=_FULL_REPO, pattern="origin", path=".git/config", output_mode="count")

    assert marker_result == {
        "text": "",
        "files_matched": 0,
        "warnings": [],
        "partial": False,
    }
    assert git_result == {
        "text": "",
        "files_matched": 0,
        "warnings": [],
        "partial": False,
    }


def test_gitlab_grep_supports_match_all_shortcut_compile_errors_and_warning_paths(monkeypatch: Any) -> None:
    provider = _provider(make_runtime_config(max_output_chars=50))
    monkeypatch.setattr(provider, "_get_project_default_branch", lambda repo: "main")
    monkeypatch.setattr(provider, "_ensure_local_checkout", lambda **kwargs: None)
    monkeypatch.setattr(provider, "_grep_via_search_api", lambda **kwargs: None)
    monkeypatch.setattr(
        provider,
        "_get_repository_files",
        lambda **kwargs: [
            {"path": "/src/app.py", "is_binary": False, "sha": "sha-app"},
            {"path": "/src/util.py", "is_binary": False, "sha": "sha-util"},
        ],
    )

    def _fake_file_text(*, file_path: str, **kwargs: Any) -> str:
        if file_path == "/src/app.py":
            return "ok\nerror"
        raise RuntimeError("denied")

    monkeypatch.setattr(provider, "_get_file_text", _fake_file_text)

    shortcut = provider.grep(repo=_FULL_REPO, pattern=".*", output_mode="files_with_matches")
    assert shortcut == {
        "text": "/src/app.py\n/src/util.py",
        "files_matched": 2,
        "warnings": [],
        "partial": False,
    }

    result = provider.grep(repo=_FULL_REPO, pattern="error", output_mode="count", case_insensitive=False, context_lines=0)

    assert result["text"] == "/src/app.py:1"
    assert result["files_matched"] == 1
    assert result["warnings"] == ["failed to read /src/util.py: denied"]
    assert result["partial"] is True

    compile_error = provider.grep(repo=_FULL_REPO, pattern="[")
    assert compile_error["text"].startswith("Error: Invalid regex pattern")


def test_gitlab_grep_returns_guard_result_without_reading_large_scopes(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider(make_runtime_config(grep_max_files=1))
    monkeypatch.setenv("GITLAB_GREP_USE_LOCAL_CACHE", "true")
    monkeypatch.setenv("SMITH_GITLAB_GREP_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(provider, "_get_project_default_branch", lambda repo: "main")
    monkeypatch.setattr(provider, "_grep_via_search_api", lambda **kwargs: None)
    checkout_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        provider,
        "_get_repository_files",
        lambda **kwargs: [
            {"path": "/src/app.py", "is_binary": False, "sha": "sha-app"},
            {"path": "/src/util.py", "is_binary": False, "sha": "sha-util"},
        ],
    )
    monkeypatch.setattr(
        provider,
        "_ensure_local_checkout",
        lambda **kwargs: checkout_calls.append(kwargs) or None,
    )
    monkeypatch.setattr(
        provider,
        "_get_file_text",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not read file content")),
    )

    result = provider.grep(repo=_FULL_REPO, pattern="error")

    assert result["files_matched"] == 0
    assert result["partial"] is True
    assert result["warnings"] == [
        "candidate file count 2 exceeds SMITH_GREP_MAX_FILES=1; narrow with --path/--glob or start with `smith code search`."
    ]
    assert checkout_calls == [{"repo": _FULL_REPO, "branch": "main", "sparse_patterns": None}]
    assert "Search scope contains 2 candidate files which exceeds the safety limit (1)." in result["text"]
    assert 'smith code search "<query>"' in result["text"]


def test_gitlab_list_pull_requests_uses_combined_single_repo_stream_for_mixed_statuses(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []

    newest_active_mr = {
        "iid": 1,
        "title": "Newest active MR",
        "state": "opened",
        "draft": False,
        "author": {"username": "alice"},
        "created_at": "2025-01-10T00:00:00Z",
        "closed_at": None,
        "merged_at": None,
        "source_branch": "feature/one",
        "target_branch": "main",
        "labels": ["bug"],
        "project_id": 101,
    }
    merged_mr = {
        "iid": 2,
        "title": "Merged MR",
        "state": "merged",
        "draft": False,
        "author": {"username": "bob"},
        "created_at": "2025-01-09T00:00:00Z",
        "closed_at": "2025-01-10T00:00:00Z",
        "merged_at": "2025-01-10T00:00:00Z",
        "source_branch": "feature/two",
        "target_branch": "main",
        "labels": ["enhancement"],
        "project_id": 101,
    }
    older_active_mr = {
        "iid": 3,
        "title": "Older active MR",
        "state": "opened",
        "draft": False,
        "author": {"username": "carol"},
        "created_at": "2025-01-08T00:00:00Z",
        "closed_at": None,
        "merged_at": None,
        "source_branch": "feature/three",
        "target_branch": "main",
        "labels": [],
        "project_id": 101,
    }
    closed_mr = {
        "iid": 4,
        "title": "Closed MR",
        "state": "closed",
        "draft": False,
        "author": {"username": "dave"},
        "created_at": "2025-01-07T00:00:00Z",
        "closed_at": "2025-01-08T00:00:00Z",
        "merged_at": None,
        "source_branch": "feature/four",
        "target_branch": "main",
        "labels": [],
        "project_id": 101,
    }
    oldest_active_mr = {
        "iid": 5,
        "title": "Oldest active MR",
        "state": "opened",
        "draft": False,
        "author": {"username": "erin"},
        "created_at": "2025-01-06T00:00:00Z",
        "closed_at": None,
        "merged_at": None,
        "source_branch": "feature/five",
        "target_branch": "main",
        "labels": [],
        "project_id": 101,
    }

    def _fake_request(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        calls.append({"method": method, "path": path, "params": params})
        state = (params or {}).get("state")
        if state == "all":
            return [newest_active_mr, merged_mr, older_active_mr, closed_mr, oldest_active_mr]
        if state == "opened":
            return [newest_active_mr, older_active_mr, oldest_active_mr]
        if state == "merged":
            return [merged_mr]
        if state == "closed":
            return [closed_mr]
        return []

    monkeypatch.setattr(provider, "_request", _fake_request)

    result = provider.list_pull_requests(
        repos=[_FULL_REPO],
        statuses=["active", "completed", "abandoned"],
        creators=None,
        date_from=None,
        date_to=None,
        skip=0,
        take=2,
        exclude_drafts=True,
        include_labels=True,
    )

    assert [entry["status"] for entry in result["results"]] == ["active", "completed"]
    assert result["results"][0]["labels"] == ["bug"]
    assert result["results"][1]["labels"] == ["enhancement"]
    assert result["returned_count"] == 2
    assert result["has_more"] is True
    assert calls == [
        {
            "method": "GET",
            "path": "/projects/gitlab-org%2Frepo-a/merge_requests",
            "params": {"state": "all", "scope": "all", "per_page": 100, "page": 1},
        }
    ]


def test_gitlab_search_pull_requests_uses_search_endpoint_and_maps_results(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []

    active_mr = {
        "iid": 1,
        "title": "Rollout fix",
        "state": "opened",
        "draft": False,
        "author": {"username": "alice"},
        "created_at": "2025-01-10T00:00:00Z",
        "closed_at": None,
        "merged_at": None,
        "source_branch": "feature/rollout",
        "target_branch": "main",
        "labels": ["bug"],
        "project_id": 101,
        "references": {"full": "gitlab-org/repo-a!1"},
    }
    merged_mr = {
        "iid": 2,
        "title": "Merged rollout",
        "state": "merged",
        "draft": False,
        "author": {"username": "bob"},
        "created_at": "2025-01-09T00:00:00Z",
        "closed_at": "2025-01-10T00:00:00Z",
        "merged_at": "2025-01-10T00:00:00Z",
        "source_branch": "feature/merged",
        "target_branch": "main",
        "labels": ["enhancement"],
        "project_id": 102,
        "references": {"full": "gitlab-org/repo-b!2"},
    }
    draft_mr = {
        "iid": 3,
        "title": "Draft rollout",
        "state": "opened",
        "draft": True,
        "author": {"username": "carol"},
        "created_at": "2025-01-08T00:00:00Z",
        "closed_at": None,
        "merged_at": None,
        "source_branch": "feature/draft",
        "target_branch": "main",
        "labels": ["wip"],
        "project_id": 103,
        "references": {"full": "gitlab-org/repo-c!3"},
    }

    def _fake_request(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        calls.append({"method": method, "path": path, "params": params})
        if path == "/groups/gitlab-org/merge_requests":
            return [active_mr, merged_mr, draft_mr]
        raise AssertionError(f"unexpected request: {method} {path} {params}")

    monkeypatch.setattr(provider, "_request", _fake_request)

    result = provider.search_pull_requests(
        query="rollout",
        repos=None,
        statuses=["active", "completed"],
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
                "project_name": "gitlab-org",
                "repository_name": "repo-a",
                "repository_id": 101,
                "closed_date": None,
                "source_branch": "feature/rollout",
                "target_branch": "main",
                "target_ref": "main",
                "labels": ["bug"],
            },
            {
                "pr_id": 2,
                "title": "Merged rollout",
                "created_by": "bob",
                "status": "completed",
                "creation_date": "2025-01-09T00:00:00Z",
                "project_name": "gitlab-org",
                "repository_name": "repo-b",
                "repository_id": 102,
                "closed_date": "2025-01-10",
                "source_branch": "feature/merged",
                "target_branch": "main",
                "target_ref": "main",
                "labels": ["enhancement"],
            },
        ],
    }
    assert calls == [
        {
            "method": "GET",
            "path": "/groups/gitlab-org/merge_requests",
            "params": {
                "state": "all",
                "scope": "all",
                "search": "rollout",
                "order_by": "created_at",
                "sort": "desc",
                "per_page": 100,
                "page": 1,
            },
        }
    ]


def test_gitlab_search_pull_requests_queries_all_requested_repos_before_paging(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []

    older_repo_a_mr = {
        "iid": 1,
        "title": "Older repo-a match",
        "state": "opened",
        "draft": False,
        "author": {"username": "alice"},
        "created_at": "2025-01-09T00:00:00Z",
        "closed_at": None,
        "merged_at": None,
        "source_branch": "feature/a",
        "target_branch": "main",
        "labels": [],
        "project_id": 101,
    }
    newer_repo_b_mr = {
        "iid": 2,
        "title": "Newer repo-b match",
        "state": "opened",
        "draft": False,
        "author": {"username": "bob"},
        "created_at": "2025-01-10T00:00:00Z",
        "closed_at": None,
        "merged_at": None,
        "source_branch": "feature/b",
        "target_branch": "main",
        "labels": [],
        "project_id": 102,
    }

    def _fake_request(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        calls.append({"method": method, "path": path, "params": params})
        if path == "/projects/gitlab-org%2Frepo-a/merge_requests":
            return [older_repo_a_mr]
        if path == "/projects/gitlab-org%2Frepo-b/merge_requests":
            return [newer_repo_b_mr]
        raise AssertionError(f"unexpected request: {method} {path} {params}")

    monkeypatch.setattr(provider, "_request", _fake_request)

    result = provider.search_pull_requests(
        query="rollout",
        repos=["gitlab-org/repo-a", "gitlab-org/repo-b"],
        statuses=["active"],
        creators=None,
        date_from=None,
        date_to=None,
        skip=0,
        take=1,
        exclude_drafts=True,
        include_labels=False,
    )

    assert result["results"] == [
        {
            "pr_id": 2,
            "title": "Newer repo-b match",
            "created_by": "bob",
            "status": "active",
            "creation_date": "2025-01-10T00:00:00Z",
            "project_name": "gitlab-org",
            "repository_name": "repo-b",
            "repository_id": 102,
            "closed_date": None,
            "source_branch": "feature/b",
            "target_branch": "main",
            "target_ref": "main",
            "labels": [],
        }
    ]
    assert [call["path"] for call in calls] == [
        "/projects/gitlab-org%2Frepo-a/merge_requests",
        "/projects/gitlab-org%2Frepo-b/merge_requests",
    ]


def test_gitlab_search_pull_requests_dedupes_repo_filters(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []
    repo_a_mr = {
        "iid": 1,
        "title": "Rollout fix",
        "state": "opened",
        "draft": False,
        "author": {"username": "alice"},
        "created_at": "2025-01-10T00:00:00Z",
        "closed_at": None,
        "merged_at": None,
        "source_branch": "feature/a",
        "target_branch": "main",
        "labels": [],
        "project_id": 101,
    }

    def _fake_request(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        calls.append({"method": method, "path": path, "params": params})
        if path == "/projects/gitlab-org%2Frepo-a/merge_requests":
            return [repo_a_mr]
        raise AssertionError(f"unexpected request: {method} {path} {params}")

    monkeypatch.setattr(provider, "_request", _fake_request)

    result = provider.search_pull_requests(
        query="rollout",
        repos=["gitlab-org/repo-a", "gitlab-org/repo-a"],
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
    assert [call["path"] for call in calls] == ["/projects/gitlab-org%2Frepo-a/merge_requests"]


def test_gitlab_merge_request_views_build_logs_and_grep(monkeypatch: Any) -> None:
    provider = _provider()

    def _fake_request_json(method: str, path: str, **kwargs: Any) -> Any:
        if path.endswith("/merge_requests/17"):
            return {
                "iid": 17,
                "title": "Fix it",
                "state": "opened",
                "author": {"username": "alice"},
                "source_branch": "feature",
                "target_branch": "main",
            }
        if path.endswith("/merge_requests/17/changes"):
            return {
                "changes": [
                    {"new_path": "src/app.py", "diff": "@@ -1 +1 @@\n-old\n+new"},
                    {"new_path": "src/util.py", "diff": "@@ -1 +1 @@\n-before\n+after"},
                ]
            }
        if path.endswith("/pipelines/55"):
            return {
                "id": 55,
                "iid": 12,
                "status": "success",
                "ref": "main",
                "sha": "abc123",
                "name": "CI",
            }
        raise AssertionError(f"unexpected request_json: {path}")

    def _fake_paginated_list(path: str, **kwargs: Any) -> list[dict[str, Any]]:
        if path.endswith("/pipelines/55/jobs"):
            return [{"id": 88, "started_at": "2025-01-01T00:00:00Z", "stage": "test", "name": "linux", "web_url": "https://job"}]
        if path.endswith("/merge_requests/17/discussions"):
            return [
                {
                    "id": "discussion-1",
                    "resolved": False,
                    "notes": [
                        {
                            "id": 1,
                            "body": "General note",
                            "author": {"username": "alice"},
                            "created_at": "2025-01-01T00:00:00Z",
                            "updated_at": "2025-01-01T00:00:00Z",
                        }
                    ],
                },
                {
                    "id": "discussion-2",
                    "resolved": True,
                    "position": {"new_path": "src/app.py", "new_line": 18},
                    "notes": [
                        {
                            "id": 2,
                            "body": "Inline",
                            "author": {"username": "bob"},
                            "created_at": "2025-01-02T00:00:00Z",
                            "updated_at": "2025-01-02T00:00:00Z",
                        }
                    ],
                },
            ]
        raise AssertionError(f"unexpected paginated list: {path}")

    monkeypatch.setattr(provider, "_request_json", _fake_request_json)
    monkeypatch.setattr(provider, "_get_paginated_list", _fake_paginated_list)

    pr = provider.get_pull_request(repo=_FULL_REPO, pull_request_id=17)
    threads = provider.get_pull_request_threads(repo=_FULL_REPO, pull_request_id=17)
    build = provider.get_build_log(repo=_FULL_REPO, build_id=55)

    assert pr == {
        "pull_request": {
            "pullRequestId": 17,
            "title": "Fix it",
            "status": "active",
            "createdBy": {"displayName": "alice"},
            "sourceRefName": "refs/heads/feature",
            "targetRefName": "refs/heads/main",
        },
        "threads": threads["threads"],
        "changed_files": ["src/app.py", "src/util.py"],
        "diffs": {
            "src/app.py": "@@ -1 +1 @@\n-old\n+new",
            "src/util.py": "@@ -1 +1 @@\n-before\n+after",
        },
    }
    assert threads["returned_count"] == 2
    assert threads["total_comments"] == 2
    assert threads["threads"][1]["file_path"] == "/src/app.py"
    assert threads["threads"][1]["line_start"] == 18
    assert build == {
        "metadata": {
            "project_name": "gitlab-org",
            "build_id": 55,
            "build_number": 12,
            "status": "success",
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
                "stage_name": "test",
                "job_name": "linux",
                "step_name": None,
            }
        ],
    }

    monkeypatch.setattr(provider, "get_build_log", lambda **kwargs: {"logs": [{"id": 88}, {"id": 89}]})
    monkeypatch.setattr(provider, "get_build_log_content", lambda *, log_id, **kwargs: "ok\nerror" if log_id == 89 else "ok")
    grep = provider.grep_build_log(repo=_FULL_REPO, build_id=55, pattern="error", output_mode="logs_with_matches")

    assert grep == {
        "text": "Log 89",
        "logs_matched": 1,
        "warnings": [],
        "partial": False,
    }


def test_gitlab_issue_search_ticket_mapping_and_my_work_items(monkeypatch: Any) -> None:
    provider = _provider()
    captured_calls: list[dict[str, Any]] = []

    def _fake_paginated_list(path: str, *, params: dict[str, Any] | None = None, limit: int | None = None) -> Any:
        captured_calls.append({"path": path, "params": params, "limit": limit})
        return [
            {
                "iid": 10,
                "title": "Incident",
                "state": "opened",
                "assignees": [{"username": "alice"}],
                "labels": ["sev1"],
                "created_at": "2025-01-01T00:00:00Z",
                "updated_at": "2025-01-02T00:00:00Z",
                "web_url": "https://gitlab.com/gitlab-org/repo-a/-/issues/10",
                "project_id": 101,
            }
        ]

    monkeypatch.setattr(provider, "_get_paginated_list", _fake_paginated_list)
    monkeypatch.setattr(provider, "_request_json", lambda method, path, **kwargs: {
        "iid": 10,
        "web_url": "https://gitlab.com/gitlab-org/repo-a/-/issues/10",
        "state": "closed",
        "title": "Incident",
    })
    monkeypatch.setattr(provider, "_project_path_from_id", lambda project_id: "gitlab-org/repo-a")

    search = provider.search_work_items(
        query="incident",
        project=_FULL_REPO,
        repo=None,
        state="open",
        assigned_to="alice",
        skip=0,
        take=1,
        include_closed=False,
    )
    ticket = provider.get_ticket_by_id(repo=_FULL_REPO, work_item_id=10)

    assert captured_calls == [
        {
            "path": "/projects/gitlab-org%2Frepo-a/issues",
            "params": {
                "scope": "all",
                "search": "incident",
                "state": "opened",
                "assignee_username": "alice",
            },
            "limit": 2,
        }
    ]
    assert search == {
        "matchesCount": 1,
        "returned_count": 1,
        "has_more": False,
        "results": [
            {
                "id": 10,
                "title": "Incident",
                "state": "Open",
                "type": "Issue",
                "project": "gitlab-org",
                "assigned_to": "alice",
                "tags": ["sev1"],
                "created_date": "2025-01-01T00:00:00Z",
                "changed_date": "2025-01-02T00:00:00Z",
                "url": "https://gitlab.com/gitlab-org/repo-a/-/issues/10",
                "repository": "repo-a",
                "highlights": [],
            }
        ],
    }
    assert ticket == {
        "id": 10,
        "url": "https://gitlab.com/gitlab-org/repo-a/-/issues/10",
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
    mine = provider.get_my_work_items(project=_FULL_REPO, repo=None, include_closed=True, skip=2, take=3)

    assert forwarded == {
        "query": "",
        "project": _FULL_REPO,
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


def test_gitlab_issue_search_matches_count_uses_full_query_total(monkeypatch: Any) -> None:
    provider = _provider()
    issues = [
        {
            "iid": 1,
            "title": "Issue 1",
            "state": "opened",
            "assignees": [],
            "labels": [],
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
            "web_url": "https://gitlab.com/gitlab-org/repo-a/-/issues/1",
            "project_id": 101,
        },
        {
            "iid": 2,
            "title": "Issue 2",
            "state": "opened",
            "assignees": [],
            "labels": [],
            "created_at": "2025-01-02T00:00:00Z",
            "updated_at": "2025-01-02T00:00:00Z",
            "web_url": "https://gitlab.com/gitlab-org/repo-a/-/issues/2",
            "project_id": 101,
        },
        {
            "iid": 3,
            "title": "Issue 3",
            "state": "opened",
            "assignees": [],
            "labels": [],
            "created_at": "2025-01-03T00:00:00Z",
            "updated_at": "2025-01-03T00:00:00Z",
            "web_url": "https://gitlab.com/gitlab-org/repo-a/-/issues/3",
            "project_id": 101,
        },
        {
            "iid": 4,
            "title": "Issue 4",
            "state": "opened",
            "assignees": [],
            "labels": [],
            "created_at": "2025-01-04T00:00:00Z",
            "updated_at": "2025-01-04T00:00:00Z",
            "web_url": "https://gitlab.com/gitlab-org/repo-a/-/issues/4",
            "project_id": 101,
        },
        {
            "iid": 5,
            "title": "Issue 5",
            "state": "opened",
            "assignees": [],
            "labels": [],
            "created_at": "2025-01-05T00:00:00Z",
            "updated_at": "2025-01-05T00:00:00Z",
            "web_url": "https://gitlab.com/gitlab-org/repo-a/-/issues/5",
            "project_id": 101,
        },
        {
            "iid": 6,
            "title": "Issue 6",
            "state": "opened",
            "assignees": [],
            "labels": [],
            "created_at": "2025-01-06T00:00:00Z",
            "updated_at": "2025-01-06T00:00:00Z",
            "web_url": "https://gitlab.com/gitlab-org/repo-a/-/issues/6",
            "project_id": 101,
        },
    ]
    calls: list[dict[str, Any]] = []

    def _fake_paginated_list(path: str, *, params: dict[str, Any] | None = None, limit: int | None = None) -> Any:
        calls.append({"path": path, "params": params, "limit": limit})
        if limit is None:
            return issues
        return issues[:limit]

    monkeypatch.setattr(provider, "_get_paginated_list", _fake_paginated_list)

    result = provider.search_work_items(
        query="issue",
        repo=_FULL_REPO,
        skip=1,
        take=2,
    )

    assert [item["id"] for item in result["results"]] == [2, 3]
    assert result["returned_count"] == 2
    assert result["has_more"] is True
    assert result["matchesCount"] == 6
    assert calls == [
        {
            "path": "/projects/gitlab-org%2Frepo-a/issues",
            "params": {"scope": "all", "search": "issue"},
            "limit": 4,
        },
        {
            "path": "/projects/gitlab-org%2Frepo-a/issues",
            "params": {"scope": "all", "search": "issue"},
            "limit": None,
        },
    ]


_ROOT_PROJECT_TOKEN = "gitlab-org%2Frepo-a"
_CHECKOUT_PROJECT_TOKEN = "gitlab-org%2Fcheckout-service"
_INTEGRATION_PROJECT_TOKEN = "gitlab-org%2Fintegration-tests"


def test_gitlab_list_pipelines_traverses_bridges_and_normalizes_downstream(monkeypatch: Any) -> None:
    provider = _provider()
    provider._cache_project(project_id="101", full_path=_FULL_REPO)
    provider._cache_project(project_id="202", full_path="gitlab-org/checkout-service")
    provider._cache_project(project_id="303", full_path="gitlab-org/integration-tests")

    pipeline_details = {
        (_ROOT_PROJECT_TOKEN, 77): {
            "id": 77,
            "project_id": 101,
            "iid": 12,
            "name": "Main CI",
            "status": "running",
            "ref": "main",
            "sha": "a1b2c3dEEEEEEE",
            "source": "push",
            "created_at": "2025-01-01T00:00:00Z",
            "duration": 1842,
            "web_url": "https://gitlab.com/gitlab-org/repo-a/-/pipelines/77",
        },
        (_CHECKOUT_PROJECT_TOKEN, 88): {
            "id": 88,
            "project_id": 202,
            "iid": 5,
            "name": "Checkout build",
            "status": "success",
            "ref": "main",
            "sha": "9f8e7d6EEEEEEE",
            "source": "pipeline",
            "created_at": "2025-01-01T00:05:00Z",
            "duration": 420,
            "web_url": "https://gitlab.com/gitlab-org/checkout-service/-/pipelines/88",
        },
        (_INTEGRATION_PROJECT_TOKEN, 99): {
            "id": 99,
            "project_id": 303,
            "iid": 2,
            "name": "Integration tests",
            "status": "failed",
            "ref": "main",
            "sha": "9f8e7d6EEEEEEE",
            "source": "parent_pipeline",
            "created_at": "2025-01-01T00:10:00Z",
            "duration": 98,
            "web_url": "https://gitlab.com/gitlab-org/integration-tests/-/pipelines/99",
        },
    }

    def _fake_request_json(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        assert method == "GET"
        for (project_token, pipeline_id), detail in pipeline_details.items():
            if path == f"/projects/{project_token}/pipelines/{pipeline_id}":
                return detail
        raise AssertionError(f"unexpected request: {path}")

    def _fake_get_paginated_list(path: str, **kwargs: Any) -> list[dict[str, Any]]:
        if path == f"/projects/{_ROOT_PROJECT_TOKEN}/pipelines/77/bridges":
            return [
                {
                    "id": 501,
                    "name": "trigger:checkout",
                    "downstream_pipeline": {"id": 88, "project_id": 202},
                }
            ]
        if path == f"/projects/{_CHECKOUT_PROJECT_TOKEN}/pipelines/88/bridges":
            return [
                {
                    "id": 502,
                    "name": "trigger:integration",
                    "downstream_pipeline": {"id": 99, "project_id": 303},
                }
            ]
        if path == f"/projects/{_INTEGRATION_PROJECT_TOKEN}/pipelines/99/bridges":
            return []
        raise AssertionError(f"unexpected bridges path: {path}")

    monkeypatch.setattr(provider, "_request_json", _fake_request_json)
    monkeypatch.setattr(provider, "_get_paginated_list", _fake_get_paginated_list)

    query = PipelineListQuery.create()
    result = provider.list_pipelines(repo=_FULL_REPO, pipeline_id=77, query=query)

    assert [row["id"] for row in result["pipelines"]] == [77, 88, 99]
    assert [row["depth"] for row in result["pipelines"]] == [0, 1, 2]
    assert [row["project"] for row in result["pipelines"]] == [
        _FULL_REPO,
        "gitlab-org/checkout-service",
        "gitlab-org/integration-tests",
    ]
    assert [row["trigger_job"] for row in result["pipelines"]] == [
        None,
        "trigger:checkout",
        "trigger:integration",
    ]
    assert [row["parent_id"] for row in result["pipelines"]] == [None, 77, 88]
    assert result["pipelines"][0]["name"] == "Main CI"
    assert result["pipelines"][0]["duration_s"] == 1842
    assert result["returned_count"] == 3
    assert result["total_count"] == 3
    # GraphQL path fails because the test only stubs REST GETs; we exercise
    # the REST fallback which adds a partial flag + warning.
    assert result["partial"] is True
    assert any(
        "GraphQL unavailable" in warning for warning in result["warnings"]
    )


def test_gitlab_list_pipelines_applies_grep_status_and_max_depth(monkeypatch: Any) -> None:
    provider = _provider()
    provider._cache_project(project_id="101", full_path=_FULL_REPO)
    provider._cache_project(project_id="202", full_path="gitlab-org/checkout-service")

    root_detail = {
        "id": 77,
        "project_id": 101,
        "name": "Main CI",
        "status": "running",
        "ref": "main",
        "sha": "abc1234",
        "source": "push",
        "duration": 100,
    }
    downstream_detail = {
        "id": 88,
        "project_id": 202,
        "name": "Checkout build",
        "status": "failed",
        "ref": "main",
        "sha": "def5678",
        "source": "pipeline",
        "duration": 50,
    }

    bridges_calls: list[str] = []

    def _fake_request_json(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if path == f"/projects/{_ROOT_PROJECT_TOKEN}/pipelines/77":
            return root_detail
        if path == f"/projects/{_CHECKOUT_PROJECT_TOKEN}/pipelines/88":
            return downstream_detail
        raise AssertionError(f"unexpected request: {path}")

    def _fake_get_paginated_list(path: str, **kwargs: Any) -> list[dict[str, Any]]:
        bridges_calls.append(path)
        if path == f"/projects/{_ROOT_PROJECT_TOKEN}/pipelines/77/bridges":
            return [
                {
                    "id": 501,
                    "name": "trigger:checkout",
                    "downstream_pipeline": {"id": 88, "project_id": 202},
                }
            ]
        raise AssertionError(f"unexpected bridges path: {path}")

    monkeypatch.setattr(provider, "_request_json", _fake_request_json)
    monkeypatch.setattr(provider, "_get_paginated_list", _fake_get_paginated_list)

    query = PipelineListQuery.create(
        grep="checkout",
        statuses=["failed"],
        max_depth=1,
    )
    result = provider.list_pipelines(repo=_FULL_REPO, pipeline_id=77, query=query)

    assert [row["id"] for row in result["pipelines"]] == [88]
    assert result["pipelines"][0]["trigger_job"] == "trigger:checkout"
    assert result["total_count"] == 1
    # Only one bridge level fetched because max_depth=1.
    assert bridges_calls == [f"/projects/{_ROOT_PROJECT_TOKEN}/pipelines/77/bridges"]


def test_gitlab_resolve_downstream_rows_reuses_project_lookup_for_siblings(
    monkeypatch: Any,
) -> None:
    provider = _provider()
    project_id_calls: list[str] = []

    def _fake_project_path_from_id(project_id: str | int | None) -> str | None:
        project_id_calls.append(str(project_id))
        return "gitlab-org/checkout-service"

    def _fake_request_json(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        assert method == "GET"
        if path == f"/projects/{_CHECKOUT_PROJECT_TOKEN}/pipelines/88":
            return {
                "id": 88,
                "project_id": 202,
                "name": "Checkout build",
                "status": "success",
                "ref": "main",
                "sha": "9f8e7d6EEEEEEE",
                "source": "pipeline",
            }
        if path == f"/projects/{_CHECKOUT_PROJECT_TOKEN}/pipelines/89":
            return {
                "id": 89,
                "project_id": 202,
                "name": "Checkout build (retry)",
                "status": "failed",
                "ref": "main",
                "sha": "8e7d6c5EEEEEEE",
                "source": "pipeline",
            }
        raise AssertionError(f"unexpected request: {path}")

    monkeypatch.setattr(provider, "_project_path_from_id", _fake_project_path_from_id)
    monkeypatch.setattr(provider, "_request_json", _fake_request_json)

    rows = provider._resolve_downstream_rows(
        lookups=[
            ({"id": 77}, {"id": 88, "project_id": 202}, "trigger:checkout", "deploy"),
            ({"id": 77}, {"id": 89, "project_id": 202}, "trigger:checkout-retry", "deploy"),
        ],
        depth=1,
    )

    assert project_id_calls == ["202"]
    assert [row["id"] for row in rows] == [88, 89]
    assert [row["project"] for row in rows] == [
        "gitlab-org/checkout-service",
        "gitlab-org/checkout-service",
    ]


def test_gitlab_list_pipelines_warns_when_max_depth_stops_traversal(monkeypatch: Any) -> None:
    provider = _provider()
    provider._cache_project(project_id="101", full_path=_FULL_REPO)
    provider._cache_project(project_id="202", full_path="gitlab-org/checkout-service")

    detail_101_77 = {
        "id": 77,
        "project_id": 101,
        "name": "Main CI",
        "status": "running",
        "ref": "main",
        "sha": "abc1234",
        "source": "push",
    }
    detail_202_88 = {
        "id": 88,
        "project_id": 202,
        "name": "Checkout build",
        "status": "running",
        "ref": "main",
        "sha": "def5678",
        "source": "pipeline",
    }

    def _fake_request_json(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if path == f"/projects/{_ROOT_PROJECT_TOKEN}/pipelines/77":
            return detail_101_77
        if path == f"/projects/{_CHECKOUT_PROJECT_TOKEN}/pipelines/88":
            return detail_202_88
        raise AssertionError(f"unexpected request: {path}")

    def _fake_get_paginated_list(path: str, **kwargs: Any) -> list[dict[str, Any]]:
        if path == f"/projects/{_ROOT_PROJECT_TOKEN}/pipelines/77/bridges":
            return [
                {
                    "name": "trigger:checkout",
                    "downstream_pipeline": {"id": 88, "project_id": 202},
                }
            ]
        raise AssertionError(f"unexpected bridges path: {path}")

    monkeypatch.setattr(provider, "_request_json", _fake_request_json)
    monkeypatch.setattr(provider, "_get_paginated_list", _fake_get_paginated_list)

    query = PipelineListQuery.create(max_depth=1)
    result = provider.list_pipelines(repo=_FULL_REPO, pipeline_id=77, query=query)

    assert [row["id"] for row in result["pipelines"]] == [77, 88]
    assert any("max depth 1 reached" in warning for warning in result["warnings"])
    assert result["partial"] is True


def _graphql_stage_node(
    name: str,
    jobs: list[dict[str, Any]],
    *,
    group_name: str | None = None,
) -> dict[str, Any]:
    """Build a GitLab GraphQL stage node. Wraps jobs in a single group to match the real schema:
    ``stages.nodes[].groups.nodes[].jobs.nodes[]``.
    """
    return {
        "name": name,
        "groups": {
            "nodes": [
                {
                    "name": group_name or name,
                    "jobs": {"nodes": jobs},
                }
            ]
        },
    }


def _graphql_pipeline_node(
    *,
    pipeline_id: int,
    iid: int,
    project_id: int,
    project_path: str,
    name: str | None = None,
    commit_title: str | None = None,
    ref: str,
    status: str,
    duration: int | None,
    stages: list[dict[str, Any]],
    downstream: list[dict[str, Any]] | None = None,
    web_url: str | None = None,
) -> dict[str, Any]:
    return {
        "id": f"gid://gitlab/Ci::Pipeline/{pipeline_id}",
        "iid": str(iid),
        "name": name,
        "ref": ref,
        "status": status,
        "sha": "deadbeef",
        "commit": {"title": commit_title} if commit_title is not None else None,
        "duration": duration,
        "createdAt": "2025-04-01T00:00:00Z",
        "webUrl": web_url or f"https://gitlab.example/{project_path}/-/pipelines/{pipeline_id}",
        "project": {
            "id": f"gid://gitlab/Project/{project_id}",
            "fullPath": project_path,
        },
        "stages": {"nodes": stages},
        "downstream": {"nodes": downstream or []},
    }


def _graphql_job_node(
    *,
    job_id: int,
    name: str,
    status: str,
    duration: int | None = None,
    allow_failure: bool = False,
    manual: bool = False,
    environment: str | None = None,
    needs: list[str] | None = None,
    downstream: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": f"gid://gitlab/Ci::Build/{job_id}",
        "name": name,
        "status": status,
        "duration": duration,
        "allowFailure": allow_failure,
        "manualJob": manual,
        "webPath": f"/project/-/jobs/{job_id}",
        "environment": {"name": environment} if environment else None,
        "previousStageJobsOrNeeds": {
            "nodes": [{"name": need} for need in (needs or [])]
        },
        "downstreamPipeline": downstream,
    }


def test_gitlab_list_pipelines_graphql_populates_stages_jobs_and_downstream(
    monkeypatch: Any,
) -> None:
    provider = _provider()

    root_node = _graphql_pipeline_node(
        pipeline_id=998877,
        iid=12,
        project_id=882,
        project_path="acme/api",
        name="Main CI",
        ref="feat/api",
        status="RUNNING",
        duration=None,
        stages=[
            _graphql_stage_node(
                "build",
                [
                    _graphql_job_node(
                        job_id=10,
                        name="compile",
                        status="SUCCESS",
                        duration=200,
                    )
                ],
            ),
            _graphql_stage_node(
                "test",
                [
                    _graphql_job_node(
                        job_id=21,
                        name="unit 1/2",
                        status="SUCCESS",
                        duration=60,
                        needs=["compile"],
                    ),
                    _graphql_job_node(
                        job_id=22,
                        name="unit 2/2",
                        status="SUCCESS",
                        duration=65,
                        needs=["compile"],
                    ),
                    _graphql_job_node(
                        job_id=23,
                        name="lint",
                        status="FAILED",
                        duration=40,
                        allow_failure=True,
                        needs=["compile"],
                    ),
                ],
            ),
            _graphql_stage_node(
                "deploy",
                [
                    _graphql_job_node(
                        job_id=30,
                        name="staging_up",
                        status="SUCCESS",
                        duration=300,
                        environment="staging",
                        needs=["unit"],
                    ),
                    _graphql_job_node(
                        job_id=31,
                        name="prod_up",
                        status="MANUAL",
                        duration=0,
                        manual=True,
                        environment="prod",
                        needs=["staging_up"],
                        downstream={
                            "id": "gid://gitlab/Ci::Pipeline/1122",
                            "iid": "1",
                            "status": "CREATED",
                            "project": {
                                "id": "gid://gitlab/Project/900",
                                "fullPath": "ops/infra",
                            },
                        },
                    ),
                ],
            ),
        ],
    )
    downstream_node = _graphql_pipeline_node(
        pipeline_id=1122,
        iid=1,
        project_id=900,
        project_path="ops/infra",
        ref="main",
        status="CREATED",
        duration=None,
        stages=[],
    )

    calls: list[str] = []

    def _fake_graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        lookup = str(variables.get("id") or f"iid:{variables.get('iid')}")
        calls.append(lookup)
        if variables.get("id") == "gid://gitlab/Ci::Pipeline/998877":
            return {"ciPipeline": root_node}
        if variables.get("iid") == "1":
            return {"ciPipeline": downstream_node}
        raise AssertionError(f"unexpected GraphQL vars: {variables}")

    monkeypatch.setattr(provider, "_graphql", _fake_graphql)

    result = provider.list_pipelines(
        repo="acme/api",
        pipeline_id=998877,
        query=PipelineListQuery.create(),
    )

    assert calls == [
        "gid://gitlab/Ci::Pipeline/998877",
        "iid:1",
    ]
    assert [row["id"] for row in result["pipelines"]] == [998877, 1122]
    assert [row["project"] for row in result["pipelines"]] == ["acme/api", "ops/infra"]
    assert [row["project_id"] for row in result["pipelines"]] == [882, 900]
    assert result["pipelines"][0]["name"] == "Main CI"
    assert [row["depth"] for row in result["pipelines"]] == [0, 1]
    assert [row["parent_id"] for row in result["pipelines"]] == [None, 998877]
    assert result["pipelines"][1]["trigger_job"] == "prod_up"
    assert result["partial"] is False

    root_jobs = result["pipelines"][0]["jobs"]
    assert [job["id"] for job in root_jobs] == [10, 21, 22, 23, 30, 31]
    assert [job["stage"] for job in root_jobs] == [
        "build",
        "test",
        "test",
        "test",
        "deploy",
        "deploy",
    ]
    unit_1 = next(j for j in root_jobs if j["id"] == 21)
    assert unit_1["matrix"] == [1, 2]
    assert unit_1["name"] == "unit"
    assert unit_1["needs"] == ["compile"]
    assert unit_1["status"] == "success"

    lint_job = next(j for j in root_jobs if j["id"] == 23)
    assert lint_job["allow_failure"] is True
    assert lint_job["status"] == "failed"

    prod_job = next(j for j in root_jobs if j["id"] == 31)
    assert prod_job["manual"] is True
    assert prod_job["environment"] == "prod"
    assert prod_job["status"] == "manual"
    assert prod_job["downstream"] == {
        "project": "ops/infra",
        "pipeline_id": 1122,
        "status": "created",
    }

    # Downstream pipeline rendered as its own node in the flat list.
    assert result["pipelines"][1]["jobs"] == []


def test_gitlab_list_pipelines_graphql_respects_max_depth(monkeypatch: Any) -> None:
    provider = _provider()

    root_node = _graphql_pipeline_node(
        pipeline_id=77,
        iid=1,
        project_id=101,
        project_path=_FULL_REPO,
        ref="main",
        status="RUNNING",
        duration=100,
        stages=[
            _graphql_stage_node(
                "trigger",
                [
                    _graphql_job_node(
                        job_id=500,
                        name="trigger:checkout",
                        status="SUCCESS",
                        duration=5,
                        downstream={
                            "id": "gid://gitlab/Ci::Pipeline/88",
                            "iid": "2",
                            "status": "RUNNING",
                            "project": {
                                "id": "gid://gitlab/Project/202",
                                "fullPath": "gitlab-org/checkout-service",
                            },
                        },
                    )
                ],
            )
        ],
    )

    def _fake_graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        if variables.get("id") == "gid://gitlab/Ci::Pipeline/77":
            return {"ciPipeline": root_node}
        raise AssertionError(f"should not traverse beyond root: {variables}")

    monkeypatch.setattr(provider, "_graphql", _fake_graphql)

    query = PipelineListQuery.create(max_depth=1)
    result = provider.list_pipelines(repo=_FULL_REPO, pipeline_id=77, query=query)

    assert [row["id"] for row in result["pipelines"]] == [77]
    assert any("max depth 1 reached" in warning for warning in result["warnings"])
    assert result["partial"] is True


def test_gitlab_graphql_url_derives_from_api_v4_base() -> None:
    provider = _provider()

    assert provider._graphql_url() == "https://gitlab.com/api/graphql"


def test_gitlab_list_pipelines_graphql_traverses_groups_per_stage(
    monkeypatch: Any,
) -> None:
    """Regression: GitLab's GraphQL schema nests jobs under ``stages[].groups[].jobs[]``.

    A prior implementation queried ``stages[].jobs[]`` directly which returned an
    empty list on real GitLab even when the pipeline had jobs. This test locks in
    the group-level traversal so a stage with multiple job base-names still renders
    every job.
    """
    provider = _provider()

    root_node = {
        "id": "gid://gitlab/Ci::Pipeline/500",
        "iid": "3",
        "ref": "main",
        "status": "FAILED",
        "sha": "cafe",
        "duration": 120,
        "createdAt": "2025-05-01T00:00:00Z",
        "webUrl": "https://gitlab.example/repo/-/pipelines/500",
        "project": {"id": "gid://gitlab/Project/44", "fullPath": "g/repo"},
        "stages": {
            "nodes": [
                {
                    "name": "test",
                    "groups": {
                        "nodes": [
                            {
                                "name": "unit",
                                "jobs": {
                                    "nodes": [
                                        _graphql_job_node(
                                            job_id=201,
                                            name="unit 1/2",
                                            status="SUCCESS",
                                            duration=30,
                                        ),
                                        _graphql_job_node(
                                            job_id=202,
                                            name="unit 2/2",
                                            status="SUCCESS",
                                            duration=31,
                                        ),
                                    ]
                                },
                            },
                            {
                                "name": "lint",
                                "jobs": {
                                    "nodes": [
                                        _graphql_job_node(
                                            job_id=203,
                                            name="lint",
                                            status="FAILED",
                                            duration=12,
                                        )
                                    ]
                                },
                            },
                        ]
                    },
                }
            ]
        },
        "downstream": {"nodes": []},
    }

    monkeypatch.setattr(
        provider,
        "_graphql",
        lambda query, variables: {"ciPipeline": root_node},
    )

    result = provider.list_pipelines(
        repo="g/repo",
        pipeline_id=500,
        query=PipelineListQuery.create(),
    )

    jobs = result["pipelines"][0]["jobs"]
    assert [job["id"] for job in jobs] == [201, 202, 203]
    assert [job["stage"] for job in jobs] == ["test", "test", "test"]
    assert [job["name"] for job in jobs] == ["unit", "unit", "lint"]
    assert [job["matrix"] for job in jobs] == [[1, 2], [2, 2], None]


def test_gitlab_list_pipelines_graphql_caches_compat_query_after_schema_mismatch(
    monkeypatch: Any,
) -> None:
    provider = _provider()

    root_node = _graphql_pipeline_node(
        pipeline_id=998877,
        iid=12,
        project_id=882,
        project_path="acme/api",
        ref="feat/api",
        status="RUNNING",
        duration=305,
        stages=[
            _graphql_stage_node(
                "trigger",
                [
                    _graphql_job_node(
                        job_id=31,
                        name="prod_up",
                        status="MANUAL",
                        duration=0,
                        manual=True,
                        needs=["compile"],
                        downstream={
                            "id": "gid://gitlab/Ci::Pipeline/1122",
                            "iid": "1",
                            "status": "CREATED",
                            "project": {
                                "id": "gid://gitlab/Project/900",
                                "fullPath": "ops/infra",
                            },
                        },
                    )
                ],
            )
        ],
    )
    downstream_node = _graphql_pipeline_node(
        pipeline_id=1122,
        iid=1,
        project_id=900,
        project_path="ops/infra",
        ref="main",
        status="CREATED",
        duration=None,
        stages=[],
    )

    compat_root = dict(root_node)
    compat_root.pop("webUrl", None)
    compat_root["path"] = "/acme/api/-/pipelines/998877"
    compat_downstream = dict(downstream_node)
    compat_downstream.pop("webUrl", None)
    compat_downstream["path"] = "/ops/infra/-/pipelines/1122"

    calls: list[dict[str, Any]] = []

    def _fake_graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        calls.append({"query": query, "variables": dict(variables)})
        if "environment { name }" in query:
            raise SmithApiError("GitLab GraphQL error: Field 'environment' doesn't exist on type 'CiJob'")
        if variables.get("id") == "gid://gitlab/Ci::Pipeline/998877":
            return {"project": {"pipeline": compat_root}}
        if variables.get("iid") == "1":
            return {"project": {"pipeline": compat_downstream}}
        raise AssertionError(f"unexpected GraphQL vars: {variables}")

    monkeypatch.setattr(provider, "_graphql", _fake_graphql)

    result = provider.list_pipelines(
        repo="acme/api",
        pipeline_id=998877,
        query=PipelineListQuery.create(),
    )

    assert [row["id"] for row in result["pipelines"]] == [998877, 1122]
    assert [row["project"] for row in result["pipelines"]] == ["acme/api", "ops/infra"]
    assert result["pipelines"][0]["url"] == "https://gitlab.com/acme/api/-/pipelines/998877"
    assert result["pipelines"][1]["trigger_job"] == "prod_up"
    assert result["pipelines"][0]["jobs"][0]["stage"] == "trigger"
    assert result["pipelines"][0]["jobs"][0]["needs"] == ["compile"]
    assert result["pipelines"][0]["jobs"][0]["environment"] is None
    assert result["partial"] is False

    assert [call["variables"]["fullPath"] for call in calls] == [
        "acme/api",
        "acme/api",
        "ops/infra",
    ]
    assert calls[0]["variables"]["id"] == "gid://gitlab/Ci::Pipeline/998877"
    assert calls[1]["variables"]["id"] == "gid://gitlab/Ci::Pipeline/998877"
    assert calls[2]["variables"]["iid"] == "1"
    assert "environment { name }" in calls[0]["query"]
    assert all("environment { name }" not in call["query"] for call in calls[1:])
    assert provider._gitlab_pipeline_graphql_variant == "compat"


def test_gitlab_list_pipelines_graphql_uses_commit_title_and_downstream_source_job(
    monkeypatch: Any,
) -> None:
    provider = _provider()

    root_node = _graphql_pipeline_node(
        pipeline_id=500,
        iid=3,
        project_id=44,
        project_path="g/repo",
        commit_title="Release train",
        ref="main",
        status="FAILED",
        duration=120,
        stages=[],
        downstream=[
            {
                "id": "gid://gitlab/Ci::Pipeline/501",
                "iid": "4",
                "status": "FAILED",
                "project": {
                    "id": "gid://gitlab/Project/44",
                    "fullPath": "g/repo",
                },
                "sourceJob": {
                    "name": "release-pipeline",
                    "stage": {"name": "deploy"},
                },
            }
        ],
    )
    child_node = _graphql_pipeline_node(
        pipeline_id=501,
        iid=4,
        project_id=44,
        project_path="g/repo",
        commit_title="Release train",
        ref="main",
        status="FAILED",
        duration=30,
        stages=[],
    )

    def _fake_graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        if variables.get("id") == "gid://gitlab/Ci::Pipeline/500":
            return {"ciPipeline": root_node}
        if variables.get("iid") == "4":
            return {"ciPipeline": child_node}
        raise AssertionError(f"unexpected GraphQL vars: {variables}")

    monkeypatch.setattr(provider, "_graphql", _fake_graphql)

    result = provider.list_pipelines(
        repo="g/repo",
        pipeline_id=500,
        query=PipelineListQuery.create(),
    )

    assert [row["id"] for row in result["pipelines"]] == [500, 501]
    assert result["pipelines"][0]["name"] == "Release train"
    assert result["pipelines"][1]["name"] == "Release train"
    assert result["pipelines"][1]["trigger_job"] == "release-pipeline"
    assert result["pipelines"][1]["trigger_stage"] == "deploy"
