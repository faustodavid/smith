from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from tests.support import make_runtime_config

from smith.errors import SmithApiError, SmithAuthError, SmithError
from smith.providers.gitlab import GitLabProvider

_FULL_REPO = "gitlab-org/repo-a"


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


def test_gitlab_token_rejects_empty_cli_token(monkeypatch: Any) -> None:
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
    repo_id = provider._project_id(_FULL_REPO)

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

    assert provider._full_project_path(_FULL_REPO) == _FULL_REPO
    assert provider._full_project_path("gitlab-org/sub/repo-a") == "gitlab-org/sub/repo-a"
    assert provider._relative_repo_path("gitlab-org/sub/repo-a") == "gitlab-org/sub/repo-a"

    assert provider._get_repository_files(repo=_FULL_REPO, path=None, branch="main") == [
        {"path": "/README.md", "is_binary": False, "sha": "sha-readme"}
    ]
    assert provider._get_repository_files(repo=_FULL_REPO, path="/src", branch="main") == [
        {"path": "/src/app.py", "is_binary": False, "sha": "sha-app"}
    ]
    assert provider._get_repository_files(repo=_FULL_REPO, path="/README.md", branch="main") == [
        {"path": "/README.md", "is_binary": False, "sha": "sha-readme"}
    ]


def test_gitlab_file_text_prefers_blob_api_and_falls_back_to_raw_file(monkeypatch: Any) -> None:
    provider = _provider()
    repo_id = provider._project_id(_FULL_REPO)
    calls: list[str] = []

    def _blob_success(method: str, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        calls.append(path)
        if path == f"/projects/{repo_id}/repository/blobs/sha-1/raw":
            return "hello from blob"
        raise AssertionError(f"unexpected request_text: {path} {params}")

    monkeypatch.setattr(provider, "_request_text", _blob_success)
    assert provider._get_file_text(repo=_FULL_REPO, file_path="/src/app.py", branch="main", blob_sha="sha-1") == "hello from blob"

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
            repo=_FULL_REPO,
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


def test_gitlab_local_checkout_clone_uses_token_auth_when_available(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    git_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(provider, "_gitlab_grep_cache_root", lambda: str(tmp_path))
    monkeypatch.setattr(provider, "_get_token", lambda force_refresh=False: "env-token")
    monkeypatch.setattr(provider, "_checkout_local_ref", lambda *args, **kwargs: None)
    monkeypatch.setattr(provider, "_reset_local_checkout", lambda *args, **kwargs: None)
    monkeypatch.setattr(provider, "_mark_local_checkout_refreshed", lambda *args, **kwargs: None)

    def _fake_run(args: list[str], **kwargs: Any) -> Any:
        git_calls.append({"args": args, "env": kwargs.get("env")})
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("smith.providers.gitlab_code.subprocess.run", _fake_run)

    checkout_dir = provider._ensure_local_checkout(repo=_FULL_REPO, branch="main")
    expected_basic = base64.b64encode(b"oauth2:env-token").decode("ascii")

    assert checkout_dir == provider._local_checkout_path(repo=_FULL_REPO, branch="main")
    assert git_calls == [
        {
            "args": [
                "git",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "credential.interactive=never",
                "-c",
                f"http.extraHeader=Authorization: Basic {expected_basic}",
                "clone",
                "--filter=blob:none",
                "--depth",
                "1",
                "--branch",
                "main",
                "--single-branch",
                "https://gitlab.com/gitlab-org/repo-a.git",
                provider._local_checkout_path(repo=_FULL_REPO, branch="main"),
            ],
            "env": {**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        }
    ]


def test_gitlab_local_checkout_fetch_uses_token_auth_when_available(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    monkeypatch.setattr(provider, "_gitlab_grep_cache_root", lambda: str(tmp_path))
    checkout_dir = provider._local_checkout_path(repo=_FULL_REPO, branch="main")
    Path(checkout_dir, ".git").mkdir(parents=True)
    git_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(provider, "_get_token", lambda force_refresh=False: "env-token")
    monkeypatch.setattr(provider, "_local_checkout_has_expected_origin", lambda *args, **kwargs: True)
    monkeypatch.setattr(provider, "_local_checkout_needs_refresh", lambda *args, **kwargs: True)
    monkeypatch.setattr(provider, "_remote_head_sha", lambda *args, **kwargs: None)
    monkeypatch.setattr(provider, "_local_head_sha", lambda *args, **kwargs: None)
    monkeypatch.setattr(provider, "_checkout_local_ref", lambda *args, **kwargs: None)
    monkeypatch.setattr(provider, "_reset_local_checkout", lambda *args, **kwargs: None)
    monkeypatch.setattr(provider, "_mark_local_checkout_refreshed", lambda *args, **kwargs: None)

    def _fake_run(args: list[str], **kwargs: Any) -> Any:
        git_calls.append({"args": args, "env": kwargs.get("env")})
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("smith.providers.gitlab_code.subprocess.run", _fake_run)

    result = provider._ensure_local_checkout(repo=_FULL_REPO, branch="main")
    expected_basic = base64.b64encode(b"oauth2:env-token").decode("ascii")

    assert result == checkout_dir
    assert git_calls == [
        {
            "args": [
                "git",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "credential.interactive=never",
                "-c",
                f"http.extraHeader=Authorization: Basic {expected_basic}",
                "-C",
                checkout_dir,
                "fetch",
                "--filter=blob:none",
                "--depth",
                "1",
                "origin",
                "main",
            ],
            "env": {**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        }
    ]


def test_gitlab_remote_head_sha_uses_token_auth_when_available(monkeypatch: Any) -> None:
    provider = _provider()
    checkout_dir = os.path.join("tmp", "checkout")
    git_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(provider, "_get_token", lambda force_refresh=False: "env-token")

    def _fake_run(args: list[str], **kwargs: Any) -> Any:
        git_calls.append({"args": args, "env": kwargs.get("env")})
        return SimpleNamespace(stdout="abc123\trefs/heads/main\n", stderr="", returncode=0)

    monkeypatch.setattr("smith.providers.gitlab_code.subprocess.run", _fake_run)

    expected_basic = base64.b64encode(b"oauth2:env-token").decode("ascii")

    assert provider._remote_head_sha(checkout_dir, "main") == "abc123"
    assert git_calls == [
        {
            "args": [
                "git",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "credential.interactive=never",
                "-c",
                f"http.extraHeader=Authorization: Basic {expected_basic}",
                "-C",
                checkout_dir,
                "ls-remote",
                "origin",
                "main",
            ],
            "env": {**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        }
    ]


def test_gitlab_apply_sparse_patterns_uses_token_auth_when_available(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    checkout_dir = tmp_path / "checkout"
    (checkout_dir / ".git").mkdir(parents=True)
    git_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(provider, "_get_token", lambda force_refresh=False: "env-token")

    def _fake_run(args: list[str], **kwargs: Any) -> Any:
        git_calls.append({"args": args, "env": kwargs.get("env")})
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("smith.providers.gitlab_code.subprocess.run", _fake_run)

    expected_basic = base64.b64encode(b"oauth2:env-token").decode("ascii")

    provider._apply_sparse_patterns(str(checkout_dir), ["/*", "**/*.yml"])

    assert git_calls == [
        {
            "args": [
                "git",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "credential.interactive=never",
                "-c",
                f"http.extraHeader=Authorization: Basic {expected_basic}",
                "-C",
                str(checkout_dir),
                "sparse-checkout",
                "set",
                "--no-cone",
                "/*",
                "**/*.yml",
            ],
            "env": {**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        }
    ]


def test_gitlab_checkout_and_reset_use_token_auth_when_available(monkeypatch: Any) -> None:
    provider = _provider()
    checkout_dir = os.path.join("tmp", "checkout")
    git_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(provider, "_get_token", lambda force_refresh=False: "env-token")

    def _fake_run(args: list[str], **kwargs: Any) -> Any:
        git_calls.append({"args": args, "env": kwargs.get("env")})
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("smith.providers.gitlab_code.subprocess.run", _fake_run)

    expected_basic = base64.b64encode(b"oauth2:env-token").decode("ascii")

    provider._checkout_local_ref(checkout_dir, "FETCH_HEAD")
    provider._reset_local_checkout(checkout_dir)

    assert git_calls == [
        {
            "args": [
                "git",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "credential.interactive=never",
                "-c",
                f"http.extraHeader=Authorization: Basic {expected_basic}",
                "-C",
                checkout_dir,
                "checkout",
                "--force",
                "--detach",
                "FETCH_HEAD",
            ],
            "env": {**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        },
        {
            "args": [
                "git",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "credential.interactive=never",
                "-c",
                f"http.extraHeader=Authorization: Basic {expected_basic}",
                "-C",
                checkout_dir,
                "reset",
                "--hard",
                "HEAD",
            ],
            "env": {**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        },
        {
            "args": [
                "git",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "credential.interactive=never",
                "-c",
                f"http.extraHeader=Authorization: Basic {expected_basic}",
                "-C",
                checkout_dir,
                "clean",
                "-fd",
            ],
            "env": {**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        },
    ]


def test_gitlab_grep_no_clone_skips_local_checkout(monkeypatch: Any) -> None:
    provider = _provider()
    monkeypatch.setenv("GITLAB_GREP_USE_LOCAL_CACHE", "true")
    monkeypatch.setattr(provider, "_get_project_default_branch", lambda repo: "main")
    monkeypatch.setattr(
        provider,
        "_ensure_local_checkout",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("local checkout should be skipped")),
    )
    monkeypatch.setattr(
        provider,
        "_get_repository_files",
        lambda **kwargs: [{"path": "/src/app.py", "is_binary": False, "sha": "sha-app"}],
    )
    monkeypatch.setattr(provider, "_get_file_text", lambda **kwargs: "needle\n")

    result = provider.grep(repo=_FULL_REPO, pattern="needle", output_mode="count", no_clone=True)

    assert result == {
        "text": "/src/app.py:1",
        "files_matched": 1,
        "warnings": [],
        "partial": False,
    }


def test_gitlab_grep_does_not_retry_local_checkout_after_api_listing(monkeypatch: Any) -> None:
    provider = _provider()
    checkout_calls: list[dict[str, Any]] = []

    monkeypatch.setenv("GITLAB_GREP_USE_LOCAL_CACHE", "true")
    monkeypatch.setattr(provider, "_get_project_default_branch", lambda repo: "main")
    monkeypatch.setattr(provider, "_ensure_local_checkout", lambda **kwargs: checkout_calls.append(kwargs) or None)
    monkeypatch.setattr(provider, "_grep_via_search_api", lambda **kwargs: None)
    monkeypatch.setattr(
        provider,
        "_get_repository_files",
        lambda **kwargs: [{"path": "/src/app.py", "is_binary": False, "sha": "sha-app"}],
    )
    monkeypatch.setattr(provider, "_get_file_text", lambda **kwargs: "needle\n")

    result = provider.grep(repo=_FULL_REPO, pattern="needle", output_mode="count")

    assert result == {
        "text": "/src/app.py:1",
        "files_matched": 1,
        "warnings": [],
        "partial": False,
    }
    assert checkout_calls == [{"repo": _FULL_REPO, "branch": "main", "sparse_patterns": None}]


def test_gitlab_grep_parallel_api_fallback_uses_worker_sessions(monkeypatch: Any) -> None:
    provider = _provider()
    worker_sessions: list[object] = []

    monkeypatch.setenv("GITLAB_GREP_USE_LOCAL_CACHE", "true")
    monkeypatch.setenv("GITLAB_GREP_ENABLE_PARALLEL", "true")
    monkeypatch.setenv("GITLAB_GREP_MAX_WORKERS", "2")
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

    def _fake_get_http_session(*, session: Any | None = None) -> object:
        assert session is None
        worker_session = object()
        worker_sessions.append(worker_session)
        return worker_session

    def _fake_get_file_text(*, file_path: str, session: object | None = None, **kwargs: Any) -> str:
        assert session in worker_sessions
        return "needle\n" if file_path in {"/src/app.py", "/src/util.py"} else "miss\n"

    monkeypatch.setattr(provider, "_get_http_session", _fake_get_http_session)
    monkeypatch.setattr(provider, "_get_file_text", _fake_get_file_text)

    result = provider.grep(repo=_FULL_REPO, pattern="needle", output_mode="count")

    assert result == {
        "text": "/src/app.py:1\n/src/util.py:1",
        "files_matched": 2,
        "warnings": [],
        "partial": False,
    }
    assert len(worker_sessions) == 2


def test_gitlab_grep_content_uses_search_api_prefilter_when_local_checkout_unavailable(monkeypatch: Any) -> None:
    provider = _provider()

    monkeypatch.setenv("GITLAB_GREP_USE_LOCAL_CACHE", "true")
    monkeypatch.setattr(provider, "_get_project_default_branch", lambda repo: "main")
    monkeypatch.setattr(provider, "_ensure_local_checkout", lambda **kwargs: None)
    monkeypatch.setattr(
        provider,
        "_grep_via_search_api",
        lambda **kwargs: [{"path": "/src/app.py", "is_binary": False, "sha": "sha-app", "local_path": None}],
    )
    monkeypatch.setattr(
        provider,
        "_get_repository_files",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("API listing should be skipped")),
    )
    monkeypatch.setattr(provider, "_get_file_text", lambda **kwargs: "needle\n")

    result = provider.grep(repo=_FULL_REPO, pattern="needle", output_mode="content", context_lines=0)

    assert result == {
        "text": "/src/app.py\n1:needle",
        "files_matched": 1,
        "warnings": [],
        "partial": False,
    }


def test_gitlab_compute_sparse_patterns_narrows_by_path_and_simple_glob() -> None:
    assert GitLabProvider._compute_sparse_patterns(None, None) is None
    assert GitLabProvider._compute_sparse_patterns("/", None) is None
    assert GitLabProvider._compute_sparse_patterns("/src", None) == ["/*", "/src/"]
    assert GitLabProvider._compute_sparse_patterns(None, "*.yml") == ["/*", "**/*.yml"]
    assert GitLabProvider._compute_sparse_patterns("/configs", "*.yml") == ["/*", "/configs/**/*.yml"]
    assert GitLabProvider._compute_sparse_patterns(None, "src/*.yml") is None
    assert GitLabProvider._compute_sparse_patterns(None, "{*.yml,*.yaml}") is None


def test_gitlab_partial_clone_adds_sparse_flag_when_patterns_given(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    git_calls: list[list[str]] = []

    monkeypatch.setattr(provider, "_gitlab_grep_cache_root", lambda: str(tmp_path))
    monkeypatch.setattr(provider, "_get_token", lambda force_refresh=False: "env-token")
    monkeypatch.setattr(provider, "_checkout_local_ref", lambda *args, **kwargs: None)
    monkeypatch.setattr(provider, "_reset_local_checkout", lambda *args, **kwargs: None)
    monkeypatch.setattr(provider, "_mark_local_checkout_refreshed", lambda *args, **kwargs: None)

    def _fake_run(args: list[str], **kwargs: Any) -> Any:
        git_calls.append(args)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("smith.providers.gitlab_code.subprocess.run", _fake_run)

    checkout_dir = provider._ensure_local_checkout(
        repo=_FULL_REPO,
        branch="main",
        sparse_patterns=["/*", "**/*.yml"],
    )

    assert checkout_dir == provider._local_checkout_path(repo=_FULL_REPO, branch="main")
    clone_call = next(call for call in git_calls if "clone" in call)
    assert "--filter=blob:none" in clone_call
    assert "--sparse" in clone_call
    sparse_calls = [call for call in git_calls if "sparse-checkout" in call]
    assert sparse_calls, "sparse-checkout set should be invoked when patterns are provided"
    assert sparse_calls[0][-2:] == ["/*", "**/*.yml"]


def test_gitlab_ls_remote_precheck_skips_fetch_when_head_matches(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    monkeypatch.setattr(provider, "_gitlab_grep_cache_root", lambda: str(tmp_path))
    checkout_dir = provider._local_checkout_path(repo=_FULL_REPO, branch="main")
    Path(checkout_dir, ".git").mkdir(parents=True)
    monkeypatch.setattr(provider, "_local_checkout_has_expected_origin", lambda *args, **kwargs: True)
    monkeypatch.setattr(provider, "_local_checkout_needs_refresh", lambda *args, **kwargs: True)
    monkeypatch.setattr(provider, "_remote_head_sha", lambda *args, **kwargs: "abc123")
    monkeypatch.setattr(provider, "_local_head_sha", lambda *args, **kwargs: "abc123")

    mark_calls: list[str] = []
    monkeypatch.setattr(
        provider,
        "_mark_local_checkout_refreshed",
        lambda dir_: mark_calls.append(dir_),
    )
    monkeypatch.setattr(
        provider,
        "_git_auth_subprocess",
        lambda *args, **kwargs: pytest.fail("fetch should be skipped when HEAD is unchanged"),
    )

    result = provider._ensure_local_checkout(repo=_FULL_REPO, branch="main")

    assert result == checkout_dir
    assert mark_calls == [checkout_dir]


def test_gitlab_ripgrep_content_reverse_flips_blocks_within_file_only(
    monkeypatch: Any, tmp_path: Any
) -> None:
    provider = _provider()

    monkeypatch.setattr(
        "smith.providers.local_checkout.shutil.which",
        lambda name: "/usr/bin/rg" if name == "rg" else None,
    )
    stub_output = (
        f"{tmp_path}/b.py\n"
        "1-x\n"
        "2:hit\n"
        "3-y\n"
        "--\n"
        "10-x\n"
        "11:hit\n"
        "12-y\n"
        f"\n{tmp_path}/a.py\n"
        "5:hit\n"
    )
    monkeypatch.setattr(
        "smith.providers.local_checkout.subprocess.run",
        lambda args, **kwargs: SimpleNamespace(returncode=0, stdout=stub_output, stderr=""),
    )

    result = provider._ripgrep_local_result(
        checkout_dir=str(tmp_path),
        pattern="hit",
        case_insensitive=False,
        path=None,
        glob=None,
        filename_filter=re.compile(r".*"),
        output_mode="content",
        context_lines=1,
        reverse=True,
    )

    lines = result["text"].splitlines()
    assert lines[0] == "/b.py"
    sep_index = lines.index("--")
    assert lines[1:sep_index] == ["10-x", "11:hit", "12-y"]
    assert lines[sep_index + 1 : sep_index + 4] == ["1-x", "2:hit", "3-y"]
    assert lines[-2:] == ["/a.py", "5:hit"]


def test_gitlab_ripgrep_uses_sortr_when_reverse_is_requested(
    monkeypatch: Any, tmp_path: Any
) -> None:
    provider = _provider()
    rg_calls: list[list[str]] = []

    monkeypatch.setattr(
        "smith.providers.local_checkout.shutil.which",
        lambda name: "/usr/bin/rg" if name == "rg" else None,
    )

    def _fake_run(args: list[str], **kwargs: Any) -> Any:
        rg_calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("smith.providers.local_checkout.subprocess.run", _fake_run)

    provider._ripgrep_local_result(
        checkout_dir=str(tmp_path),
        pattern="x",
        case_insensitive=False,
        path=None,
        glob=None,
        filename_filter=re.compile(r".*"),
        output_mode="files_with_matches",
        context_lines=0,
        reverse=True,
    )
    assert "--sortr" in rg_calls[-1]
    assert "--sort" not in rg_calls[-1]

    provider._ripgrep_local_result(
        checkout_dir=str(tmp_path),
        pattern="x",
        case_insensitive=False,
        path=None,
        glob=None,
        filename_filter=re.compile(r".*"),
        output_mode="files_with_matches",
        context_lines=0,
        reverse=False,
    )
    assert "--sort" in rg_calls[-1]
    assert "--sortr" not in rg_calls[-1]


def test_gitlab_ripgrep_files_with_matches_uses_subprocess(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.yml").write_text("trigger: deploy\n", encoding="utf-8")
    rg_calls: list[list[str]] = []

    monkeypatch.setattr(
        "smith.providers.local_checkout.shutil.which",
        lambda name: "/usr/bin/rg" if name == "rg" else None,
    )

    def _fake_run(args: list[str], **kwargs: Any) -> Any:
        rg_calls.append(args)
        return SimpleNamespace(returncode=0, stdout=f"{tmp_path}/src/app.yml\n", stderr="")

    monkeypatch.setattr("smith.providers.local_checkout.subprocess.run", _fake_run)

    result = provider._ripgrep_local_result(
        checkout_dir=str(tmp_path),
        pattern="trigger:",
        case_insensitive=True,
        path=None,
        glob="*.yml",
        filename_filter=re.compile(r".*\.yml$"),
        output_mode="files_with_matches",
        context_lines=0,
    )

    assert result is not None
    assert result["text"] == "/src/app.yml"
    assert result["files_matched"] == 1
    assert rg_calls, "ripgrep should be invoked"
    rg_args = rg_calls[0]
    assert rg_args[0] == "/usr/bin/rg"
    assert "-l" in rg_args
    assert "-e" in rg_args
    assert "trigger:" in rg_args
    assert "*.yml" in rg_args


def test_gitlab_grep_raises_when_ripgrep_missing(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    monkeypatch.setattr(
        "smith.providers.local_checkout.shutil.which",
        lambda name: None,
    )

    with pytest.raises(SmithError) as excinfo:
        provider._ripgrep_local_result(
            checkout_dir=str(tmp_path),
            pattern="x",
            case_insensitive=True,
            path=None,
            glob=None,
            filename_filter=re.compile(r".*"),
            output_mode="files_with_matches",
            context_lines=0,
        )
    assert "ripgrep" in str(excinfo.value).lower()


def test_gitlab_ripgrep_parses_content_output_with_context(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("before\ntrigger: x\nafter\n", encoding="utf-8")

    monkeypatch.setattr(
        "smith.providers.local_checkout.shutil.which",
        lambda name: "/usr/bin/rg" if name == "rg" else None,
    )
    monkeypatch.setattr(
        "smith.providers.local_checkout.subprocess.run",
        lambda args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=f"{tmp_path}/src/app.py\n1-before\n2:trigger: x\n3-after\n",
            stderr="",
        ),
    )

    result = provider._ripgrep_local_result(
        checkout_dir=str(tmp_path),
        pattern="trigger:",
        case_insensitive=False,
        path=None,
        glob=None,
        filename_filter=re.compile(r".*"),
        output_mode="content",
        context_lines=1,
    )

    assert result is not None
    assert result["files_matched"] == 1
    assert result["text"] == "/src/app.py\n1-before\n2:trigger: x\n3-after"
