from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from tests.support import FakeResponse, make_runtime_config

from smith.errors import SmithApiError, SmithAuthError
from smith.providers.github import GitHubProvider


def _provider(config: Any | None = None) -> GitHubProvider:
    return GitHubProvider(config=config or make_runtime_config(), session=requests.Session())


def test_github_token_helpers_and_rate_limit_handling(monkeypatch: Any) -> None:
    provider = _provider()
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    monkeypatch.setattr(
        "smith.providers.github.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess should not be called")),
    )

    assert provider._get_token() == "env-token"
    assert provider._get_token() == "env-token"
    assert provider._default_accept_header() == "application/vnd.github+json"
    assert provider._default_headers() == {"X-GitHub-Api-Version": "2022-11-28"}
    assert provider._timeout() == 30
    assert provider._build_url("/repos/test") == "https://api.github.com/repos/test"

    with pytest.raises(SmithApiError, match="rate limited"):
        provider._handle_response_status(FakeResponse(429), "https://api.github.com/repos/test")


def test_github_token_falls_back_to_gh_cli_and_reports_auth_failures(monkeypatch: Any) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    provider = _provider()
    calls: list[list[str]] = []

    def _fake_run(args: list[str], **kwargs: Any) -> Any:
        calls.append(args)
        return SimpleNamespace(stdout="cli-token\n")

    monkeypatch.setattr("smith.providers.github.subprocess.run", _fake_run)

    assert provider._get_token() == "cli-token"
    assert provider._get_token() == "cli-token"
    assert calls == [["gh", "auth", "token"]]

    monkeypatch.setattr(
        "smith.providers.github.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("missing gh")),
    )
    failing_provider = _provider()

    with pytest.raises(SmithAuthError, match="Failed to acquire GitHub token"):
        failing_provider._get_token()


def test_github_token_rejects_empty_cli_token_and_exposes_auth_helpers(monkeypatch: Any) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    provider = _provider()
    monkeypatch.setattr(
        "smith.providers.github.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout="\n"),
    )

    with pytest.raises(SmithAuthError, match="GitHub token is empty"):
        provider._get_token()

    assert provider._auth_error_message() == (
        "GitHub authentication rejected with HTTP 401/403. "
        "Set GITHUB_TOKEN or run `gh auth login` and retry."
    )
    assert provider._build_url("https://example.test/repos/test") == "https://example.test/repos/test"


def test_github_requires_org_and_maps_project_repository_views(monkeypatch: Any) -> None:
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

    with pytest.raises(ValueError, match="Missing GITHUB_ORG"):
        _provider(make_runtime_config(github_org=""))._require_github_org()


def test_github_repository_file_resolution_handles_root_directory_and_file_paths(monkeypatch: Any) -> None:
    provider = _provider()
    repo_prefix = provider._repo_prefix("repo-a")

    def _fake_request_json(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        if path == f"{repo_prefix}/git/trees/main" and params == {"recursive": "1"}:
            return {"tree": [{"path": "README.md", "type": "blob", "sha": "sha-readme"}]}
        if path == f"{repo_prefix}/git/trees/main" and params is None:
            return {
                "tree": [
                    {"path": "README.md", "type": "blob", "sha": "sha-readme"},
                    {"path": "src", "type": "tree", "sha": "tree-src"},
                ]
            }
        if path == f"{repo_prefix}/git/trees/tree-src" and params == {"recursive": "1"}:
            return {"tree": [{"path": "app.py", "type": "blob", "sha": "sha-app"}]}
        raise AssertionError(f"unexpected request: {path} {params}")

    monkeypatch.setattr(provider, "_request_json", _fake_request_json)

    assert provider._get_repository_files(repo="repo-a", path=None, branch="main") == [
        {"path": "/README.md", "is_binary": False, "sha": "sha-readme"}
    ]
    assert provider._get_repository_files(repo="repo-a", path="/src", branch="main") == [
        {"path": "/src/app.py", "is_binary": False, "sha": "sha-app"}
    ]
    assert provider._get_repository_files(repo="repo-a", path="/README.md", branch="main") == [
        {"path": "/README.md", "is_binary": False, "sha": "sha-readme"}
    ]


def test_github_file_text_prefers_blob_api_and_falls_back_to_contents(monkeypatch: Any) -> None:
    provider = _provider()
    blob_value = base64.b64encode(b"hello from blob").decode("utf-8")
    contents_value = base64.b64encode(b"hello from contents").decode("utf-8")
    repo_prefix = provider._repo_prefix("repo-a")

    def _blob_success(method: str, path: str, **kwargs: Any) -> Any:
        assert path == f"{repo_prefix}/git/blobs/sha-1"
        return {"content": blob_value, "encoding": "base64"}

    monkeypatch.setattr(provider, "_request_json", _blob_success)
    assert provider._get_file_text(repo="repo-a", file_path="/src/app.py", branch="main", blob_sha="sha-1") == "hello from blob"

    calls: list[str] = []

    def _blob_fallback(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        calls.append(path)
        if path == f"{repo_prefix}/git/blobs/sha-2":
            raise RuntimeError("blob missing")
        assert params == {"ref": "main"}
        return {"content": contents_value, "encoding": "base64"}

    monkeypatch.setattr(provider, "_request_json", _blob_fallback)
    assert (
        provider._get_file_text(
            repo="repo-a",
            file_path="/src/app.py",
            branch="refs/heads/main",
            blob_sha="sha-2",
        )
        == "hello from contents"
    )
    assert calls == [f"{repo_prefix}/git/blobs/sha-2", f"{repo_prefix}/contents/src/app.py"]


def test_github_default_grep_workers_scale_by_candidate_count() -> None:
    assert GitHubProvider._default_grep_workers(1) == 1
    assert GitHubProvider._default_grep_workers(4) == 4
    assert GitHubProvider._default_grep_workers(16) == 8
    assert GitHubProvider._default_grep_workers(64) == 16
    assert GitHubProvider._default_grep_workers(128) == 20


def test_github_paginated_list_and_default_branch_cache(monkeypatch: Any) -> None:
    provider = _provider()
    request_calls: list[dict[str, Any]] = []
    page_one = [{"id": index} for index in range(100)]
    page_two = [{"id": 100}, {"id": 101}]

    def _fake_request(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        request_calls.append({"method": method, "path": path, "params": params})
        page = (params or {}).get("page")
        if page == 1:
            return page_one
        if page == 2:
            return page_two
        return []

    monkeypatch.setattr(provider, "_request", _fake_request)

    full = provider._get_paginated_list("/orgs/octo-org/repos")
    limited = provider._get_paginated_list("/orgs/octo-org/repos", limit=101)

    assert len(full) == 102
    assert len(limited) == 101
    assert request_calls[0]["params"] == {"per_page": 100, "page": 1}
    assert request_calls[1]["params"] == {"per_page": 100, "page": 2}

    branch_calls: list[str] = []

    def _fake_request_json(method: str, path: str, **kwargs: Any) -> Any:
        branch_calls.append(path)
        return {"default_branch": "develop"}

    monkeypatch.setattr(provider, "_request_json", _fake_request_json)

    assert provider._get_repository_default_branch("repo-a") == "develop"
    assert provider._get_repository_default_branch("repo-a") == "develop"
    assert branch_calls == ["/repos/octo-org/repo-a"]
