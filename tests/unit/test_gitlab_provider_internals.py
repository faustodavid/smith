from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from tests.support import make_runtime_config

from smith.errors import SmithApiError, SmithAuthError
from smith.providers.gitlab import GitLabProvider


def _provider(config: Any | None = None) -> GitLabProvider:
    return GitLabProvider(config=config or make_runtime_config(), session=requests.Session())


def test_gitlab_token_helpers_and_url_building(monkeypatch: Any) -> None:
    provider = _provider()
    monkeypatch.setenv("GITLAB_TOKEN", "env-token")
    monkeypatch.setattr(
        "smith.providers.gitlab.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess should not be called")),
    )

    assert provider._get_token() == "env-token"
    assert provider._get_token() == "env-token"
    assert provider._timeout() == 30
    assert provider._build_url("/projects") == "https://gitlab.com/api/v4/projects"
    assert provider._gitlab_host() == "gitlab.com"
    assert provider._gitlab_web_url() == "https://gitlab.com"


def test_gitlab_token_falls_back_to_glab_config_and_reports_auth_failures(monkeypatch: Any) -> None:
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    provider = _provider()
    calls: list[list[str]] = []

    def _fake_run(args: list[str], **kwargs: Any) -> Any:
        calls.append(args)
        return SimpleNamespace(stdout="cli-token\n")

    monkeypatch.setattr("smith.providers.gitlab.subprocess.run", _fake_run)

    assert provider._get_token() == "cli-token"
    assert provider._get_token() == "cli-token"
    assert calls == [["glab", "config", "get", "token", "--host", "gitlab.com"]]

    monkeypatch.setattr(
        "smith.providers.gitlab.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("missing glab")),
    )
    failing_provider = _provider()

    with pytest.raises(SmithAuthError, match="Failed to acquire GitLab token"):
        failing_provider._get_token()


def test_gitlab_token_rejects_empty_cli_token_and_requires_group(monkeypatch: Any) -> None:
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    provider = _provider()
    monkeypatch.setattr(
        "smith.providers.gitlab.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout="\n"),
    )

    with pytest.raises(SmithAuthError, match="GitLab token is empty"):
        provider._get_token()

    assert provider._auth_error_message() == (
        "GitLab authentication rejected with HTTP 401/403. "
        "Set GITLAB_TOKEN or run `glab auth login` and retry."
    )
    assert provider._build_url("https://example.test/projects") == "https://example.test/projects"

    with pytest.raises(ValueError, match="Missing GITLAB_GROUP"):
        _provider(make_runtime_config(gitlab_group=""))._require_gitlab_group()


def test_gitlab_token_uses_host_specific_glab_config_lookup(monkeypatch: Any) -> None:
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    provider = _provider(make_runtime_config(gitlab_api_url="https://gitlab.example.test/api/v4"))
    calls: list[list[str]] = []

    def _fake_run(args: list[str], **kwargs: Any) -> Any:
        calls.append(args)
        return SimpleNamespace(stdout="cli-token\n")

    monkeypatch.setattr("smith.providers.gitlab.subprocess.run", _fake_run)

    assert provider._gitlab_host() == "gitlab.example.test"
    assert provider._get_token() == "cli-token"
    assert calls == [["glab", "config", "get", "token", "--host", "gitlab.example.test"]]


def test_gitlab_project_path_helpers_and_repository_file_resolution(monkeypatch: Any) -> None:
    provider = _provider()
    repo_id = provider._project_id("repo-a")

    def _fake_request(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        if path == f"/projects/{repo_id}/repository/tree" and params == {
            "ref": "main",
            "recursive": "true",
            "per_page": 100,
            "page": 1,
        }:
            return [{"path": "README.md", "type": "blob", "id": "sha-readme"}]
        if path == f"/projects/{repo_id}/repository/tree" and params == {
            "ref": "main",
            "recursive": "true",
            "per_page": 100,
            "page": 1,
            "path": "src",
        }:
            return [{"path": "src/app.py", "type": "blob", "id": "sha-app"}]
        if path == f"/projects/{repo_id}/repository/tree" and params == {
            "ref": "main",
            "recursive": "true",
            "per_page": 100,
            "page": 1,
            "path": "README.md",
        }:
            raise SmithApiError("not a tree", status_code=404)
        raise AssertionError(f"unexpected request: {path} {params}")

    def _fake_request_json(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        if path == f"/projects/{repo_id}/repository/files/README.md" and params == {"ref": "main"}:
            return {"file_path": "README.md", "blob_id": "sha-readme"}
        raise AssertionError(f"unexpected request_json: {path} {params}")

    monkeypatch.setattr(provider, "_request", _fake_request)
    monkeypatch.setattr(provider, "_request_json", _fake_request_json)

    assert provider._full_project_path("repo-a") == "gitlab-org/repo-a"
    assert provider._full_project_path("sub/repo-a") == "gitlab-org/sub/repo-a"
    assert provider._relative_repo_path("gitlab-org/sub/repo-a") == "sub/repo-a"

    assert provider._get_repository_files(repo="repo-a", path=None, branch="main") == [
        {"path": "/README.md", "is_binary": False, "sha": "sha-readme"}
    ]
    assert provider._get_repository_files(repo="repo-a", path="/src", branch="main") == [
        {"path": "/src/app.py", "is_binary": False, "sha": "sha-app"}
    ]
    assert provider._get_repository_files(repo="repo-a", path="/README.md", branch="main") == [
        {"path": "/README.md", "is_binary": False, "sha": "sha-readme"}
    ]


def test_gitlab_file_text_prefers_blob_api_and_falls_back_to_raw_file(monkeypatch: Any) -> None:
    provider = _provider()
    repo_id = provider._project_id("repo-a")
    calls: list[str] = []

    def _blob_success(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        calls.append(path)
        if path == f"/projects/{repo_id}/repository/blobs/sha-1/raw":
            return "hello from blob"
        raise AssertionError(f"unexpected request_text: {path} {params}")

    monkeypatch.setattr(provider, "_request_text", _blob_success)
    assert provider._get_file_text(repo="repo-a", file_path="/src/app.py", branch="main", blob_sha="sha-1") == "hello from blob"

    calls.clear()

    def _blob_fallback(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        calls.append(path)
        if path == f"/projects/{repo_id}/repository/blobs/sha-2/raw":
            raise RuntimeError("blob missing")
        if path == f"/projects/{repo_id}/repository/files/src%2Fapp.py/raw":
            assert params == {"ref": "main"}
            return "hello from file"
        raise AssertionError(f"unexpected request_text: {path} {params}")

    monkeypatch.setattr(provider, "_request_text", _blob_fallback)
    assert (
        provider._get_file_text(
            repo="repo-a",
            file_path="/src/app.py",
            branch="refs/heads/main",
            blob_sha="sha-2",
        )
        == "hello from file"
    )
    assert calls == [
        f"/projects/{repo_id}/repository/blobs/sha-2/raw",
        f"/projects/{repo_id}/repository/files/src%2Fapp.py/raw",
    ]


def test_gitlab_grep_cache_max_age_defaults_to_30_seconds(monkeypatch: Any) -> None:
    provider = _provider()
    monkeypatch.delenv("GITLAB_GREP_CACHE_MAX_AGE_SECONDS", raising=False)

    assert provider._gitlab_grep_cache_max_age_seconds() == 30


def test_gitlab_grep_attempts_local_checkout_before_api_listing(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    local_file = tmp_path / "src" / "app.py"
    local_file.parent.mkdir(parents=True)
    local_file.write_text("needle\n", encoding="utf-8")
    checkout_calls: list[tuple[str, str]] = []

    monkeypatch.setenv("GITLAB_GREP_USE_LOCAL_CACHE", "true")
    monkeypatch.setattr(provider, "_get_project_default_branch", lambda repo: "main")
    monkeypatch.setattr(
        provider,
        "_ensure_local_checkout",
        lambda *, repo, branch: checkout_calls.append((repo, branch)) or str(tmp_path),
    )
    monkeypatch.setattr(
        provider,
        "_grep_via_search_api",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("Search API should not be used")),
    )
    monkeypatch.setattr(
        provider,
        "_get_local_repository_files",
        lambda **kwargs: [{"path": "/src/app.py", "is_binary": False, "sha": None, "local_path": str(local_file)}],
    )
    monkeypatch.setattr(
        provider,
        "_get_repository_files",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("API listing should not be used")),
    )
    monkeypatch.setattr(
        provider,
        "_git_grep_local",
        lambda **kwargs: {"text": "/src/app.py", "files_matched": 1, "warnings": [], "partial": False},
    )

    result = provider.grep(repo="repo-a", pattern="needle", output_mode="files_with_matches")

    assert checkout_calls == [("repo-a", "main")]
    assert result["text"] == "/src/app.py"


def test_gitlab_git_grep_local_reads_only_matched_files(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    matched_file = tmp_path / "src" / "app.py"
    matched_file.parent.mkdir(parents=True)
    matched_file.write_text("first line\nneedle\n", encoding="utf-8")
    other_file = tmp_path / "README.md"
    other_file.write_text("other line\n", encoding="utf-8")
    git_calls: list[list[str]] = []

    def _fake_git_result(args: list[str], *, cwd: str | None = None, check: bool = True) -> Any:
        del cwd
        git_calls.append(args)
        assert check is False
        return SimpleNamespace(returncode=0, stdout="src/app.py\n", stderr="")

    monkeypatch.setattr(provider, "_git_subprocess_result", _fake_git_result)

    result = provider._git_grep_local(
        checkout_dir=str(tmp_path),
        pattern="needle",
        case_insensitive=True,
        output_mode="content",
        context_lines=0,
        matching=[
            {"path": "/src/app.py", "is_binary": False, "sha": None, "local_path": str(matched_file)},
            {"path": "/README.md", "is_binary": False, "sha": None, "local_path": str(other_file)},
        ],
        search_pattern=re.compile("needle", re.IGNORECASE),
    )

    assert result == {
        "text": "/src/app.py\n2:needle",
        "files_matched": 1,
        "warnings": [],
        "partial": False,
    }
    assert "-F" in git_calls[0]
