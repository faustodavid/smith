from __future__ import annotations

import os
import re
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from tests.support import make_runtime_config

from smith.errors import SmithAuthError, SmithError
from smith.providers.azdo import ADO_SCOPE, AzdoProvider


def _provider(config: Any | None = None, credential: Any | None = None) -> AzdoProvider:
    return AzdoProvider(config=config or make_runtime_config(), credential=credential, session=requests.Session())


def test_azdo_token_helpers_and_auth_failures() -> None:
    credential_calls: list[str] = []

    class _Credential:
        def get_token(self, scope: str) -> Any:
            credential_calls.append(scope)
            return SimpleNamespace(token="ado-token")

    provider = _provider(credential=_Credential())

    assert provider._get_token() == "ado-token"
    assert provider._get_token() == "ado-token"
    assert credential_calls == [ADO_SCOPE]
    assert provider._almsearch_url("/_apis/search") == "https://almsearch.dev.azure.com/acme/_apis/search"
    assert provider._auth_error_message() == "Authentication rejected with HTTP 401/403. Run `az login` and retry."

    class _FailingCredential:
        def get_token(self, scope: str) -> Any:
            raise RuntimeError("no az login")

    failing_provider = _provider(credential=_FailingCredential())
    with pytest.raises(SmithAuthError, match="Failed to acquire Azure DevOps token"):
        failing_provider._get_token()


def test_azdo_compute_sparse_patterns_narrows_by_path_and_simple_glob() -> None:
    assert AzdoProvider._compute_sparse_patterns(None, None) is None
    assert AzdoProvider._compute_sparse_patterns("/", None) is None
    assert AzdoProvider._compute_sparse_patterns("/src", None) == ["/*", "/src/"]
    assert AzdoProvider._compute_sparse_patterns(None, "*.yml") == ["/*", "**/*.yml"]
    assert AzdoProvider._compute_sparse_patterns("/configs", "*.yml") == [
        "/*",
        "/configs/**/*.yml",
    ]
    assert AzdoProvider._compute_sparse_patterns(None, "src/*.yml") is None
    assert AzdoProvider._compute_sparse_patterns(None, "{*.yml,*.yaml}") is None


def test_azdo_partial_clone_adds_sparse_flag_when_patterns_given(
    monkeypatch: Any, tmp_path: Any
) -> None:
    provider = _provider()
    monkeypatch.setenv("AZDO_GREP_USE_LOCAL_CACHE", "true")
    monkeypatch.setenv("SMITH_AZDO_GREP_CACHE_DIR", str(tmp_path))

    git_calls: list[list[str]] = []

    def _fake_git_auth(args: list[str], *, cwd: str | None = None) -> None:
        git_calls.append(args)
        if "clone" in args:
            target = args[-1]
            os.makedirs(os.path.join(target, ".git"), exist_ok=True)

    monkeypatch.setattr(provider, "_git_auth_subprocess", _fake_git_auth)
    monkeypatch.setattr(provider, "_git_subprocess", lambda *a, **k: None)

    checkout_dir = provider._ensure_local_checkout(
        project="proj-a",
        repo="repo-a",
        branch="main",
        sparse_patterns=["/*", "**/*.yml"],
    )

    assert checkout_dir is not None
    clone_call = next(call for call in git_calls if "clone" in call)
    assert "--filter=blob:none" in clone_call
    assert "--sparse" in clone_call
    assert f"{provider.org_url}/proj-a/_git/repo-a" in clone_call


def test_azdo_ls_remote_precheck_skips_fetch_when_head_matches(
    monkeypatch: Any, tmp_path: Any
) -> None:
    provider = _provider()
    monkeypatch.setenv("AZDO_GREP_USE_LOCAL_CACHE", "true")
    monkeypatch.setenv("SMITH_AZDO_GREP_CACHE_DIR", str(tmp_path))

    checkout_dir = provider._local_checkout_path(
        project="proj-a", repo="repo-a", branch="main"
    )
    os.makedirs(os.path.join(checkout_dir, ".git"), exist_ok=True)

    monkeypatch.setattr(
        provider,
        "_local_checkout_has_expected_origin",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(provider, "_local_checkout_needs_refresh", lambda d: True)
    monkeypatch.setattr(provider, "_remote_head_sha", lambda *a, **k: "abc123")
    monkeypatch.setattr(provider, "_local_head_sha", lambda *a, **k: "abc123")
    monkeypatch.setattr(provider, "_apply_sparse_patterns", lambda *a, **k: None)

    mark_calls: list[str] = []
    monkeypatch.setattr(
        provider, "_mark_local_checkout_refreshed", lambda d: mark_calls.append(d)
    )
    monkeypatch.setattr(
        provider,
        "_git_auth_subprocess",
        lambda *a, **k: pytest.fail("fetch must be skipped when HEAD is unchanged"),
    )

    result = provider._ensure_local_checkout(
        project="proj-a", repo="repo-a", branch="main"
    )

    assert result == checkout_dir
    assert mark_calls == [checkout_dir]


def test_azdo_ripgrep_files_with_matches_uses_subprocess(
    monkeypatch: Any, tmp_path: Any
) -> None:
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


def test_azdo_ripgrep_parses_content_output_with_context(
    monkeypatch: Any, tmp_path: Any
) -> None:
    provider = _provider()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "before\ntrigger: x\nafter\n", encoding="utf-8"
    )

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


def test_azdo_grep_raises_when_ripgrep_missing(monkeypatch: Any, tmp_path: Any) -> None:
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


def test_azdo_get_repository_default_branch_caches_and_strips_refs_prefix(
    monkeypatch: Any,
) -> None:
    provider = _provider()
    calls: list[str] = []

    def _fake_request_json(method: str, url: str, **kwargs: Any) -> Any:
        calls.append(url)
        return {"defaultBranch": "refs/heads/develop"}

    monkeypatch.setattr(provider, "_request_json", _fake_request_json)

    assert provider._get_repository_default_branch("proj-a", "repo-a") == "develop"
    assert provider._get_repository_default_branch("proj-a", "repo-a") == "develop"
    assert len(calls) == 1


def test_azdo_get_repository_default_branch_falls_back_on_error(
    monkeypatch: Any,
) -> None:
    provider = _provider()

    def _fake_request_json(method: str, url: str, **kwargs: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(provider, "_request_json", _fake_request_json)
    assert provider._get_repository_default_branch("proj-a", "repo-a") == "main"


def test_azdo_grep_short_circuits_via_ripgrep_when_checkout_is_available(
    monkeypatch: Any, tmp_path: Any
) -> None:
    provider = _provider()
    monkeypatch.setenv("AZDO_GREP_USE_LOCAL_CACHE", "true")

    checkout_dir = str(tmp_path)

    monkeypatch.setattr(
        provider,
        "_get_repository_default_branch",
        lambda project, repo: "main",
    )
    monkeypatch.setattr(
        provider,
        "_ensure_local_checkout",
        lambda *, project, repo, branch, sparse_patterns=None: checkout_dir,
    )

    rg_invocations: list[dict[str, Any]] = []

    def _fake_ripgrep(*, checkout_dir: str, **kwargs: Any) -> dict[str, Any]:
        rg_invocations.append({"checkout_dir": checkout_dir, **kwargs})
        return {
            "text": "/src/app.py:1:hit",
            "files_matched": 1,
            "warnings": [],
            "partial": False,
        }

    monkeypatch.setattr(provider, "_ripgrep_local_result", _fake_ripgrep)
    monkeypatch.setattr(
        provider,
        "_get_repository_files",
        lambda **kwargs: pytest.fail("API fallback should not execute"),
    )

    result = provider.grep(project="proj-a", repo="repo-a", pattern="hit")

    assert result["files_matched"] == 1
    assert rg_invocations
    assert rg_invocations[0]["checkout_dir"] == checkout_dir
    assert rg_invocations[0]["pattern"] == "hit"


def test_azdo_grep_falls_back_to_api_when_local_cache_is_disabled(
    monkeypatch: Any,
) -> None:
    provider = _provider()
    monkeypatch.setenv("AZDO_GREP_USE_LOCAL_CACHE", "false")

    monkeypatch.setattr(
        provider,
        "_ensure_local_checkout",
        lambda *a, **k: pytest.fail("local checkout must not run when disabled"),
    )
    monkeypatch.setattr(
        provider,
        "_ripgrep_local_result",
        lambda **kwargs: pytest.fail("rg must not run when local cache is disabled"),
    )

    def _fake_get_files(*, project: str, repo: str, path: str, branch: str | None) -> list[dict[str, Any]]:
        return [
            {"path": "/src/app.py", "gitObjectType": "blob", "contentMetadata": {"isBinary": False}},
        ]

    monkeypatch.setattr(provider, "_get_repository_files", _fake_get_files)
    monkeypatch.setattr(provider, "_get_file_text", lambda **kwargs: "error\n")

    result = provider.grep(project="proj-a", repo="repo-a", pattern="error")
    assert result["files_matched"] == 1
