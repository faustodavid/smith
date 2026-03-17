from __future__ import annotations

import base64
import os
import threading
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from tests.support import FakeResponse, RecordingSession, make_runtime_config

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
    assert GitHubProvider._default_grep_workers(64) == 8
    assert GitHubProvider._default_grep_workers(128) == 8


def test_github_local_checkout_reuses_existing_clone(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    monkeypatch.setenv("GITHUB_GREP_USE_LOCAL_CACHE", "true")
    monkeypatch.setenv("SMITH_GITHUB_GREP_CACHE_DIR", str(tmp_path))

    git_calls: list[list[str]] = []

    def _fake_git(args: list[str], *, cwd: str | None = None) -> None:
        git_calls.append(args)
        if args[:2] == ["git", "clone"]:
            clone_target = args[-1]
            os.makedirs(os.path.join(clone_target, ".git"), exist_ok=True)

    monkeypatch.setattr(provider, "_git_subprocess", _fake_git)
    monkeypatch.setattr(provider, "_local_checkout_needs_refresh", lambda checkout_dir: False)

    first = provider._ensure_local_checkout(repo="repo-a", branch="main")
    second = provider._ensure_local_checkout(repo="repo-a", branch="main")

    assert first == second
    assert first is not None
    assert sum(1 for call in git_calls if call[:2] == ["git", "clone"]) == 1


def test_github_local_checkout_falls_back_when_clone_fails(monkeypatch: Any, tmp_path: Any) -> None:
    provider = _provider()
    monkeypatch.setenv("GITHUB_GREP_USE_LOCAL_CACHE", "true")
    monkeypatch.setenv("SMITH_GITHUB_GREP_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(
        provider,
        "_git_subprocess",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("clone failed")),
    )

    checkout = provider._ensure_local_checkout(repo="repo-a", branch="main")

    assert checkout is None


def test_github_local_repository_files_respects_path_scope(tmp_path: Any) -> None:
    provider = _provider()
    checkout_dir = tmp_path / "checkout"
    (checkout_dir / ".git").mkdir(parents=True)
    (checkout_dir / "src").mkdir(parents=True)
    (checkout_dir / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (checkout_dir / "README.md").write_text("hello\n", encoding="utf-8")

    all_files = provider._get_local_repository_files(checkout_dir=str(checkout_dir), path=None)
    src_files = provider._get_local_repository_files(checkout_dir=str(checkout_dir), path="/src")
    one_file = provider._get_local_repository_files(checkout_dir=str(checkout_dir), path="/README.md")

    assert {entry["path"] for entry in all_files} == {"/README.md", "/src/app.py"}
    assert {entry["path"] for entry in src_files} == {"/src/app.py"}
    assert one_file == [
        {
            "path": "/README.md",
            "is_binary": False,
            "sha": None,
            "local_path": str(checkout_dir / "README.md"),
        }
    ]


def test_github_read_local_file_text_decodes_binary_with_replacement(tmp_path: Any) -> None:
    provider = _provider()
    binary_file = tmp_path / "mixed.bin"
    binary_file.write_bytes(b"ok\xfftext")

    text = provider._read_local_file_text(str(binary_file))

    assert text == "ok\ufffdtext"


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


def test_github_repository_list_cache_returns_copies(monkeypatch: Any) -> None:
    provider = _provider()
    repo_calls: list[str] = []

    def _fake_paginated_list(path: str, **kwargs: Any) -> list[dict[str, Any]]:
        repo_calls.append(path)
        return [
            {"id": 1, "name": "repo-a", "default_branch": "main", "html_url": "https://github.com/octo-org/repo-a"}
        ]

    monkeypatch.setattr(provider, "_get_paginated_list", _fake_paginated_list)

    first = provider.list_repositories()
    first[0]["name"] = "mutated"
    second = provider.list_repositories()

    assert repo_calls == ["/orgs/octo-org/repos"]
    assert second == [
        {
            "id": 1,
            "name": "repo-a",
            "defaultBranch": "main",
            "webUrl": "https://github.com/octo-org/repo-a",
        }
    ]


def test_github_request_retries_429_using_retry_after(monkeypatch: Any) -> None:
    clock = {"now": 100.0}
    sleeps: list[float] = []

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += seconds

    session = RecordingSession(
        [
            FakeResponse(429, text='{"message":"rate limited"}', headers={"Retry-After": "12"}),
            FakeResponse(200, text='{"ok": true}', json_data={"ok": True}),
        ]
    )
    provider = GitHubProvider(config=make_runtime_config(http_retry_max_attempts=2), session=session)
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    monkeypatch.setattr("smith.providers.base.time.sleep", _sleep)
    monkeypatch.setattr("smith.providers.github.time.sleep", _sleep)
    monkeypatch.setattr("smith.providers.github.time.monotonic", lambda: clock["now"])

    result = provider._request_json("GET", "/repos/repo-a")

    assert result == {"ok": True}
    assert sleeps == [12.0]


def test_github_request_raises_rate_limit_error_after_exhausting_429_retries(monkeypatch: Any) -> None:
    clock = {"now": 100.0}
    sleeps: list[float] = []

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += seconds

    session = RecordingSession(
        [
            FakeResponse(429, text='{"message":"rate limited"}', headers={"Retry-After": "3"}),
            FakeResponse(429, text='{"message":"rate limited"}', headers={"Retry-After": "3"}),
        ]
    )
    provider = GitHubProvider(config=make_runtime_config(http_retry_max_attempts=2), session=session)
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    monkeypatch.setattr("smith.providers.base.time.sleep", _sleep)
    monkeypatch.setattr("smith.providers.github.time.sleep", _sleep)
    monkeypatch.setattr("smith.providers.github.time.monotonic", lambda: clock["now"])

    with pytest.raises(SmithApiError, match="GitHub API rate limited"):
        provider._request_json("GET", "/repos/repo-a")

    assert sleeps == [3.0]


def test_github_request_retries_secondary_rate_limit_403_without_auth_refresh(monkeypatch: Any) -> None:
    clock = {"now": 100.0}
    sleeps: list[float] = []
    token_calls: list[bool] = []

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += seconds

    session = RecordingSession(
        [
            FakeResponse(403, text='{"message":"You have exceeded a secondary rate limit."}', headers={"Retry-After": "5"}),
            FakeResponse(200, text='{"ok": true}', json_data={"ok": True}),
        ]
    )
    provider = GitHubProvider(config=make_runtime_config(http_retry_max_attempts=2), session=session)
    monkeypatch.setattr(
        provider,
        "_get_token",
        lambda *, force_refresh=False: token_calls.append(force_refresh) or "env-token",
    )
    monkeypatch.setattr("smith.providers.base.time.sleep", _sleep)
    monkeypatch.setattr("smith.providers.github.time.sleep", _sleep)
    monkeypatch.setattr("smith.providers.github.time.monotonic", lambda: clock["now"])

    result = provider._request_json("GET", "/repos/repo-a")

    assert result == {"ok": True}
    assert sleeps == [5.0]
    assert token_calls == [False, False]


def test_github_plain_403_remains_auth_failure(monkeypatch: Any) -> None:
    session = RecordingSession([FakeResponse(403, text="denied"), FakeResponse(403, text="still denied")])
    provider = GitHubProvider(config=make_runtime_config(), session=session)
    token_calls: list[bool] = []
    monkeypatch.setattr(
        provider,
        "_get_token",
        lambda *, force_refresh=False: token_calls.append(force_refresh) or "env-token",
    )

    with pytest.raises(SmithAuthError, match="GitHub authentication rejected"):
        provider._request_json("GET", "/repos/repo-a")

    assert token_calls == [False, True]


def test_github_retry_sleep_uses_rate_limit_reset_fallback(monkeypatch: Any) -> None:
    provider = _provider()
    response = FakeResponse(429, headers={"X-RateLimit-Reset": "1735689617"})

    with pytest.MonkeyPatch.context() as inner:
        from datetime import UTC, datetime

        inner.setattr("smith.http.datetime", SimpleNamespace(now=lambda tz=UTC: datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)))
        assert provider._retry_sleep_seconds(response=response, retry_index=0) == 17.0


def test_github_retry_sleep_fails_fast_when_retry_after_exceeds_limit() -> None:
    provider = _provider(make_runtime_config(github_rate_limit_max_sleep_seconds=10))
    response = FakeResponse(429, headers={"Retry-After": "11"})

    with pytest.raises(SmithApiError, match="GITHUB_RATE_LIMIT_MAX_SLEEP_SECONDS=10"):
        provider._retry_sleep_seconds(response=response, retry_index=0)


def test_github_perform_http_request_waits_for_cooldown(monkeypatch: Any) -> None:
    clock = {"now": 10.0}
    sleeps: list[float] = []

    class _Session:
        def __init__(self) -> None:
            self.calls = 0

        def request(self, method: str, url: str, **kwargs: Any) -> Any:
            self.calls += 1
            return FakeResponse(200, text='{"ok": true}', json_data={"ok": True})

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += seconds

    provider = _provider()
    provider._github_cooldown_until_monotonic = 15.0
    session = _Session()
    monkeypatch.setattr("smith.providers.github.time.sleep", _sleep)
    monkeypatch.setattr("smith.providers.github.time.monotonic", lambda: clock["now"])

    response = provider._perform_http_request(
        session,
        method="GET",
        resolved_url="https://api.github.com/repos/repo-a",
        params=None,
        json_body=None,
        headers={"Authorization": "Bearer env-token"},
        timeout=30,
    )

    assert response.status_code == 200
    assert sleeps == [5.0]
    assert session.calls == 1


def test_github_request_semaphore_serializes_requests(monkeypatch: Any) -> None:
    provider = _provider(make_runtime_config(github_max_concurrent_requests=1))
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")

    in_flight = 0
    max_in_flight = 0
    lock = threading.Lock()
    first_started = threading.Event()
    release_first = threading.Event()

    class _Session:
        def request(self, method: str, url: str, **kwargs: Any) -> Any:
            nonlocal in_flight, max_in_flight
            with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            if not first_started.is_set():
                first_started.set()
                release_first.wait(timeout=1.0)
            with lock:
                in_flight -= 1
            return FakeResponse(200, text='{"ok": true}', json_data={"ok": True})

    session = _Session()
    errors: list[Exception] = []

    def _worker() -> None:
        try:
            provider._request_json("GET", "/repos/repo-a", session=session)
        except Exception as exc:  # pragma: no cover - defensive capture
            errors.append(exc)

    first = threading.Thread(target=_worker)
    second = threading.Thread(target=_worker)
    first.start()
    first_started.wait(timeout=1.0)
    second.start()
    release_first.set()
    first.join()
    second.join()

    assert not errors
    assert max_in_flight == 1
