from __future__ import annotations

import os
from typing import Any

import pytest
import requests
from tests.support import make_runtime_config

from smith.errors import SmithApiError
from smith.providers.gitlab import GitLabProvider


def _provider(config: Any | None = None) -> GitLabProvider:
    return GitLabProvider(config=config or make_runtime_config(), session=requests.Session())


def _cache_git_output(
    provider: GitLabProvider,
    *,
    tracked_paths: list[str] | None = None,
    repo: str = "repo-a",
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


def test_gitlab_maps_group_repository_views_and_search_code(monkeypatch: Any) -> None:
    provider = _provider()
    calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        provider,
        "_get_paginated_list",
        lambda path, **kwargs: [
            {
                "id": 1,
                "path_with_namespace": "gitlab-org/repo-a",
                "default_branch": "main",
                "web_url": "https://gitlab.com/gitlab-org/repo-a",
            }
        ],
    )

    def _fake_request(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        calls.append({"method": method, "path": path, "params": params})
        return [
            {"path": "src/app.py"},
            {"path": "src/util.py"},
        ]

    monkeypatch.setattr(provider, "_request", _fake_request)

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
            "name": "repo-a",
            "defaultBranch": "main",
            "webUrl": "https://gitlab.com/gitlab-org/repo-a",
        }
    ]

    result = provider.search_code(query="grafana", project=None, repos=["repo-a"], skip=1, take=1)

    assert result == {"matchesCount": 2, "results": ["gitlab-org/repo-a:/src/util.py"]}
    assert calls == [
        {
            "method": "GET",
            "path": "/projects/gitlab-org%2Frepo-a/search",
            "params": {"scope": "blobs", "search": "grafana", "per_page": 2, "page": 1},
        }
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
        repo="repo-a",
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

    result = provider.grep(repo="repo-a", pattern="postgres", output_mode="files_with_matches")

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

    checkout_dir = provider._local_checkout_path(repo="repo-a", branch="main")
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

    result = provider.grep(repo="repo-a", pattern="error", output_mode="count")

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

    checkout_dir = provider._local_checkout_path(repo="repo-a", branch="main")
    marker_path = os.path.join(checkout_dir, ".git", "smith-last-fetch")
    os.makedirs(os.path.join(checkout_dir, ".git"), exist_ok=True)
    os.makedirs(os.path.join(checkout_dir, "src"), exist_ok=True)
    with open(marker_path, "w", encoding="utf-8"):
        pass
    with open(os.path.join(checkout_dir, "src", "app.py"), "w", encoding="utf-8") as file_handle:
        file_handle.write("error\n")
    marker_mtime = os.path.getmtime(marker_path)

    result = provider.grep(repo="repo-a", pattern="error", output_mode="count")

    assert result == {
        "text": "/src/app.py:1",
        "files_matched": 1,
        "warnings": [],
        "partial": False,
    }
    assert os.path.getmtime(marker_path) == marker_mtime
    assert any(command[-1] == "origin/main" for command in commands if "checkout" in command)
    assert all("fetch" not in command for command in commands)


def test_gitlab_grep_falls_back_to_api_when_local_checkout_unavailable(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    monkeypatch.setenv("GITLAB_GREP_USE_LOCAL_CACHE", "true")
    monkeypatch.setenv("SMITH_GITLAB_GREP_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(provider, "_get_project_default_branch", lambda repo: "main")
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

    result = provider.grep(repo="repo-a", pattern="error", output_mode="count")

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

    checkout_dir = provider._local_checkout_path(repo="repo-a", branch="main")
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

    result = provider.grep(repo="repo-a", pattern="error", output_mode="count")

    assert result == {
        "text": "/src/app.py:1",
        "files_matched": 1,
        "warnings": [],
        "partial": False,
    }


def test_gitlab_local_checkout_path_avoids_branch_collisions() -> None:
    provider = _provider()

    first = provider._local_checkout_path(repo="repo-a", branch="feature/a")
    second = provider._local_checkout_path(repo="repo-a", branch="feature_a")

    assert first != second


def test_gitlab_grep_local_checkout_rejects_path_traversal(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    monkeypatch.setenv("GITLAB_GREP_USE_LOCAL_CACHE", "true")
    monkeypatch.setenv("SMITH_GITLAB_GREP_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(provider, "_get_project_default_branch", lambda repo: "main")
    monkeypatch.setattr(provider, "_git_subprocess", lambda *args, **kwargs: None)
    monkeypatch.setattr(provider, "_git_subprocess_output", _cache_git_output(provider))

    checkout_dir = provider._local_checkout_path(repo="repo-a", branch="main")
    os.makedirs(checkout_dir, exist_ok=True)
    os.makedirs(os.path.join(checkout_dir, ".git"), exist_ok=True)
    with open(os.path.join(checkout_dir, ".git", "smith-last-fetch"), "w", encoding="utf-8"):
        pass

    secret_dir = tmp_path / "secret-area"
    secret_dir.mkdir()
    (secret_dir / "secret.txt").write_text("error\n", encoding="utf-8")

    result = provider.grep(repo="repo-a", pattern="error", path="../../secret-area", output_mode="count")

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

    checkout_dir = provider._local_checkout_path(repo="repo-a", branch="main")
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

    result = provider.grep(repo="repo-a", pattern="error", output_mode="count")

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

    checkout_dir = provider._local_checkout_path(repo="repo-a", branch="main")
    os.makedirs(checkout_dir, exist_ok=True)
    os.makedirs(os.path.join(checkout_dir, ".git"), exist_ok=True)
    os.makedirs(os.path.join(checkout_dir, "src"), exist_ok=True)
    with open(os.path.join(checkout_dir, ".git", "smith-last-fetch"), "w", encoding="utf-8"):
        pass
    with open(os.path.join(checkout_dir, "src", "app.py"), "w", encoding="utf-8") as file_handle:
        file_handle.write("ok\n")

    result = provider.grep(repo="repo-a", pattern=".*", output_mode="files_with_matches")

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

    checkout_dir = provider._local_checkout_path(repo="repo-a", branch="main")
    os.makedirs(os.path.join(checkout_dir, ".git"), exist_ok=True)
    with open(os.path.join(checkout_dir, ".git", "config"), "w", encoding="utf-8") as file_handle:
        file_handle.write("origin = secret\n")
    with open(os.path.join(checkout_dir, ".git", "smith-last-fetch"), "w", encoding="utf-8"):
        pass

    marker_result = provider.grep(
        repo="repo-a",
        pattern=".*",
        path=".git/smith-last-fetch",
        output_mode="files_with_matches",
    )
    git_result = provider.grep(repo="repo-a", pattern="origin", path=".git/config", output_mode="count")

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


def test_gitlab_grep_local_checkout_ignores_untracked_files(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    monkeypatch.setenv("GITLAB_GREP_USE_LOCAL_CACHE", "true")
    monkeypatch.setenv("SMITH_GITLAB_GREP_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(provider, "_get_project_default_branch", lambda repo: "main")
    monkeypatch.setattr(provider, "_git_subprocess", lambda *args, **kwargs: None)
    monkeypatch.setattr(provider, "_git_subprocess_output", _cache_git_output(provider, tracked_paths=["src/app.py"]))

    checkout_dir = provider._local_checkout_path(repo="repo-a", branch="main")
    os.makedirs(checkout_dir, exist_ok=True)
    os.makedirs(os.path.join(checkout_dir, ".git"), exist_ok=True)
    os.makedirs(os.path.join(checkout_dir, "src"), exist_ok=True)
    with open(os.path.join(checkout_dir, ".git", "smith-last-fetch"), "w", encoding="utf-8"):
        pass
    with open(os.path.join(checkout_dir, "src", "app.py"), "w", encoding="utf-8") as file_handle:
        file_handle.write("ok\n")
    with open(os.path.join(checkout_dir, "src", "scratch.txt"), "w", encoding="utf-8") as file_handle:
        file_handle.write("error\n")

    result = provider.grep(repo="repo-a", pattern="error", output_mode="count")

    assert result == {
        "text": "",
        "files_matched": 0,
        "warnings": [],
        "partial": False,
    }


def test_gitlab_grep_supports_match_all_shortcut_compile_errors_and_warning_paths(monkeypatch: Any) -> None:
    provider = _provider(make_runtime_config(max_output_chars=50))
    monkeypatch.setattr(provider, "_get_project_default_branch", lambda repo: "main")
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

    shortcut = provider.grep(repo="repo-a", pattern=".*", output_mode="files_with_matches")
    assert shortcut == {
        "text": "/src/app.py\n/src/util.py",
        "files_matched": 2,
        "warnings": [],
        "partial": False,
    }

    result = provider.grep(repo="repo-a", pattern="error", output_mode="count", case_insensitive=False, context_lines=0)

    assert result["text"] == "/src/app.py:1"
    assert result["files_matched"] == 1
    assert result["warnings"] == ["failed to read /src/util.py: denied"]
    assert result["partial"] is True

    compile_error = provider.grep(repo="repo-a", pattern="[")
    assert compile_error["text"].startswith("Error: Invalid regex pattern")


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
        repos=["repo-a"],
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
            return {"changes": [{"new_path": "src/app.py"}, {"new_path": "src/util.py"}]}
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

    pr = provider.get_pull_request(repo="repo-a", pull_request_id=17)
    threads = provider.get_pull_request_threads(repo="repo-a", pull_request_id=17)
    build = provider.get_build_log(repo="repo-a", build_id=55)

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
    grep = provider.grep_build_log(repo="repo-a", build_id=55, pattern="error", output_mode="logs_with_matches")

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
        project="repo-a",
        repo=None,
        state="open",
        assigned_to="alice",
        skip=0,
        take=1,
        include_closed=False,
    )
    ticket = provider.get_ticket_by_id(repo="repo-a", work_item_id=10)

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
        repo="repo-a",
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
