from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace
from typing import Any

import pytest

from smith.cli import handlers
from smith.config import RemoteConfig, SmithConfig


def _make_args(**overrides: Any) -> Namespace:
    defaults = {
        "output_format": "text",
        "remote": None,
        "provider": "azdo",
        "command_id": "orgs",
        "primary_path": "orgs",
        "alias_used": None,
        "deprecated_flags": [],
        "query": "grafana",
        "github_org": None,
        "azdo_org": None,
        "gitlab_group": None,
        "project": "proj-a",
        "repo": "repo-a",
        "repos": ["repo-a"],
        "status": ["active"],
        "creator": ["alice"],
        "date_from": "2025-01-01T00:00:00Z",
        "date_to": "2025-01-31T00:00:00Z",
        "skip": 3,
        "take": 7,
        "exclude_drafts": True,
        "include_labels": True,
        "pattern": "error",
        "path": "/src",
        "branch": "main",
        "glob": "*.py",
        "output_mode": "content",
        "case_sensitive": False,
        "context_lines": 2,
        "from_line": 10,
        "to_line": 20,
        "no_clone": False,
        "cache_provider": "all",
        "id": 42,
        "log_id": 9,
        "wiql": "SELECT [System.Id] FROM WorkItems",
        "area": "Ops",
        "type": "Bug",
        "state": "Active",
        "assigned_to": "alice",
        "include_closed": False,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


class _RecordingClient:
    def __init__(self, payload: Any | None = None) -> None:
        self.payload = payload or {"ok": True}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        if not name.startswith("execute_"):
            raise AttributeError(name)

        def _inner(**kwargs: Any) -> Any:
            self.calls.append((name, kwargs))
            return self.payload

        return _inner


def test_csv_list_and_provider_helpers() -> None:
    assert handlers._csv_list(" a, ,b , c ") == ["a", "b", "c"]
    assert handlers._selected_providers("all") == ["github", "gitlab", "azdo"]
    assert handlers._selected_providers("github") == ["github"]


def test_is_partial_result_detects_grouped_and_flat_payloads() -> None:
    grouped_warning = {
        "providers": {
            "github": {
                "ok": True,
                "warnings": ["rate limited"],
                "partial": False,
            }
        }
    }
    grouped_failure = {"providers": {"azdo": {"ok": False, "warnings": [], "partial": False}}}
    flat_warning = {"warnings": ["warning"]}
    flat_partial = {"partial": True}

    assert handlers._is_partial_result(grouped_warning) is True
    assert handlers._is_partial_result(grouped_failure) is True
    assert handlers._is_partial_result(flat_warning) is True
    assert handlers._is_partial_result(flat_partial) is True
    assert handlers._is_partial_result({"providers": []}) is False
    assert handlers._is_partial_result({"warnings": []}) is False


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (_make_args(command_id="repos", provider="all"), "does not support remote 'all'"),
        (_make_args(command_id="code.search", query="   "), "code search requires a query"),
        (_make_args(command_id="code.search", provider="github", project="proj-a"), "GitHub code search does not support `--project`"),
        (_make_args(command_id="code.search", provider="gitlab", project="proj-a"), "GitLab code search does not support `--project`"),
    ],
)
def test_validate_args_for_provider_rejects_invalid_inputs(
    args: Namespace,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        handlers.validate_args_for_provider(args)


def test_validate_args_for_provider_allows_code_search_all() -> None:
    args = _make_args(command_id="code.search", provider="all")

    handlers.validate_args_for_provider(args)


def test_validate_args_for_provider_resolves_remote_type_from_config(monkeypatch: Any) -> None:
    args = _make_args(command_id="code.search", remote="gitlab-infra", provider="", project="proj-a")
    monkeypatch.setattr(
        handlers,
        "load_config",
        lambda: SmithConfig(
            remotes={
                "gitlab-infra": RemoteConfig(
                    name="gitlab-infra",
                    provider="gitlab",
                    org="adyen",
                    host="gitlab-infra.is.adyen.com",
                    token_env="GITLAB_INFRA_READ_ONLY_TOKEN",
                    enabled=True,
                    api_url="https://gitlab-infra.is.adyen.com/api/v4",
                )
            },
            defaults={},
        ),
    )

    with pytest.raises(ValueError, match="GitLab code search does not support `--project`"):
        handlers.validate_args_for_provider(args)


def test_handle_code_grep_prefers_named_remote_over_provider(monkeypatch: Any, capsys: Any) -> None:
    client = _RecordingClient(payload={"marker": "code-grep"})
    args = _make_args(command_id="code.grep", remote="gitlab-infra", provider="gitlab", project=None)
    monkeypatch.setattr(handlers, "render_text", lambda command, data: f"{command}:{data['marker']}")

    handlers.handle_code_grep(client, args)
    _ = capsys.readouterr()

    assert client.calls == [
        (
            "execute_code_grep",
            {
                "remote_or_provider": "gitlab-infra",
                "project": None,
                "repo": "repo-a",
                "pattern": "error",
                "path": "/src",
                "branch": "main",
                "glob": "*.py",
                "output_mode": "content",
                "case_insensitive": True,
                "context_lines": 2,
                "from_line": 10,
                "to_line": 20,
                "no_clone": False,
            },
        )
    ]


def test_emit_success_supports_text_and_json_and_metadata(capsys: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(handlers, "render_text", lambda command, data: f"{command}:{data['name']}")
    json_args = _make_args(output_format="json")
    text_args = _make_args(output_format="text")

    exit_text = handlers._emit_success(args=text_args, command="orgs", data={"name": "repo-a"})
    text_output = capsys.readouterr()
    exit_json = handlers._emit_success(
        args=json_args,
        command="orgs",
        data={"name": "repo-a"},
        meta={"provider": "azdo"},
        partial=True,
    )
    json_output = capsys.readouterr()

    assert exit_text == handlers.EXIT_OK
    assert text_output.out.strip() == "orgs:repo-a"
    assert exit_json == handlers.EXIT_PARTIAL
    assert '"ok": true' in json_output.out
    assert '"provider": "azdo"' in json_output.out


def test_emit_error_supports_text_and_json(capsys: Any) -> None:
    text_args = _make_args(output_format="text")
    json_args = _make_args(output_format="json")

    exit_text = handlers._emit_error(
        args=text_args,
        command="orgs",
        code="invalid_args",
        message="bad args",
        exit_code=handlers.EXIT_INVALID_ARGS,
    )
    text_output = capsys.readouterr()
    exit_json = handlers._emit_error(
        args=json_args,
        command="orgs",
        code="invalid_args",
        message="bad args",
        exit_code=handlers.EXIT_INVALID_ARGS,
    )
    json_output = capsys.readouterr()

    assert exit_text == handlers.EXIT_INVALID_ARGS
    assert text_output.err.rstrip().endswith("bad args")
    assert exit_json == handlers.EXIT_INVALID_ARGS
    assert '"ok": false' in json_output.out
    assert '"code": "invalid_args"' in json_output.out


@pytest.mark.parametrize(
    ("handler_name", "args", "expected_method", "expected_kwargs"),
    [
        (
            "handle_discover_projects",
            _make_args(command_id="orgs"),
            "execute_discover_projects",
            {"remote_or_provider": "azdo"},
        ),
        (
            "handle_discover_repos",
            _make_args(command_id="repos"),
            "execute_discover_repos",
            {"remote_or_provider": "azdo", "project": "proj-a"},
        ),
        (
            "handle_code_search",
            _make_args(command_id="code.search", provider="all"),
            "execute_code_search",
            {
                "remote_or_provider": "all",
                "query": "grafana",
                "project": "proj-a",
                "repos": ["repo-a"],
                "skip": 3,
                "take": 7,
            },
        ),
        (
            "handle_code_grep",
            _make_args(command_id="code.grep", no_clone=True),
            "execute_code_grep",
            {
                "remote_or_provider": "azdo",
                "project": "proj-a",
                "repo": "repo-a",
                "pattern": "error",
                "path": "/src",
                "branch": "main",
                "glob": "*.py",
                "output_mode": "content",
                "case_insensitive": True,
                "context_lines": 2,
                "from_line": 10,
                "to_line": 20,
                "no_clone": True,
            },
        ),
        (
            "handle_pr_get",
            _make_args(command_id="prs.get"),
            "execute_pr_get",
            {"remote_or_provider": "azdo", "project": "proj-a", "repo": "repo-a", "pull_request_id": 42},
        ),
        (
            "handle_pr_threads",
            _make_args(command_id="prs.threads"),
            "execute_pr_threads",
            {"remote_or_provider": "azdo", "project": "proj-a", "repo": "repo-a", "pull_request_id": 42},
        ),
        (
            "handle_ci_logs",
            _make_args(command_id="pipelines.logs.list"),
            "execute_ci_logs",
            {"remote_or_provider": "azdo", "project": "proj-a", "repo": "repo-a", "build_id": 42},
        ),
        (
            "handle_ci_grep",
            _make_args(command_id="pipelines.logs.grep"),
            "execute_ci_grep",
            {
                "remote_or_provider": "azdo",
                "project": "proj-a",
                "repo": "repo-a",
                "build_id": 42,
                "log_id": 9,
                "pattern": "error",
                "output_mode": "content",
                "case_insensitive": True,
                "context_lines": 2,
                "from_line": 10,
                "to_line": 20,
            },
        ),
        (
            "handle_work_get",
            _make_args(command_id="stories.get"),
            "execute_work_get",
            {"remote_or_provider": "azdo", "project": "proj-a", "repo": "repo-a", "work_item_id": 42},
        ),
        (
            "handle_work_search",
            _make_args(command_id="stories.search"),
            "execute_work_search",
            {
                "remote_or_provider": "azdo",
                "query": "grafana",
                "project": "proj-a",
                "repo": "repo-a",
                "area": "Ops",
                "work_item_type": "Bug",
                "state": "Active",
                "assigned_to": "alice",
                "skip": 3,
                "take": 7,
            },
        ),
        (
            "handle_work_mine",
            _make_args(command_id="stories.mine"),
            "execute_work_mine",
            {
                "remote_or_provider": "azdo",
                "project": "proj-a",
                "repo": "repo-a",
                "include_closed": False,
                "skip": 3,
                "take": 7,
            },
        ),
    ],
)
def test_handlers_forward_expected_arguments(
    monkeypatch: Any,
    capsys: Any,
    handler_name: str,
    args: Namespace,
    expected_method: str,
    expected_kwargs: dict[str, Any],
) -> None:
    client = _RecordingClient(payload={"marker": handler_name})
    monkeypatch.setattr(handlers, "render_text", lambda command, data: f"{command}:{data['marker']}")

    exit_code = getattr(handlers, handler_name)(client, args)
    output = capsys.readouterr()

    assert exit_code == handlers.EXIT_OK
    assert output.out.strip() == f"{args.command_id}:{handler_name}"
    assert client.calls == [(expected_method, expected_kwargs)]


def test_handle_cache_clean_cleans_requested_provider_cache(monkeypatch: Any, capsys: Any, tmp_path: Any) -> None:
    github_cache = tmp_path / "github-grep"
    gitlab_cache = tmp_path / "gitlab-grep"
    github_cache.mkdir()
    gitlab_cache.mkdir()
    args = _make_args(command_id="cache.clean", output_format="text", cache_provider="github")

    monkeypatch.setenv("SMITH_GITHUB_GREP_CACHE_DIR", str(github_cache))
    monkeypatch.setenv("SMITH_GITLAB_GREP_CACHE_DIR", str(gitlab_cache))
    monkeypatch.setattr(handlers, "render_text", lambda command, data: f"{command}:{','.join(data['cleaned'])}")

    exit_code = handlers.handle_cache_clean(None, args)
    output = capsys.readouterr()

    assert exit_code == handlers.EXIT_OK
    assert output.out.strip() == f"cache.clean:{github_cache}"
    assert not github_cache.exists()
    assert gitlab_cache.exists()


@pytest.mark.parametrize(
    ("provider", "expected_projects", "expected_repos"),
    [
        ("azdo", ["proj-a"], ["repo-a"]),
        ("github", None, ["repo-a"]),
        ("gitlab", None, ["repo-a"]),
    ],
)
def test_handle_pr_list_branches_by_provider(
    monkeypatch: Any,
    capsys: Any,
    provider: str,
    expected_projects: list[str] | None,
    expected_repos: list[str] | None,
) -> None:
    client = _RecordingClient(payload={"marker": provider})
    args = _make_args(command_id="prs.list", provider=provider)
    monkeypatch.setattr(handlers, "render_text", lambda command, data: f"{command}:{data['marker']}")

    exit_code = handlers.handle_pr_list(client, args)
    output = capsys.readouterr()

    assert exit_code == handlers.EXIT_OK
    assert output.out.strip() == f"prs.list:{provider}"
    assert client.calls == [
        (
            "execute_pr_list",
            {
                "remote_or_provider": provider,
                "projects": expected_projects,
                "repos": expected_repos,
                "statuses": ["active"],
                "creators": ["alice"],
                "date_from": "2025-01-01T00:00:00Z",
                "date_to": "2025-01-31T00:00:00Z",
                "skip": 3,
                "take": 7,
                "exclude_drafts": True,
                "include_labels": True,
            },
        )
    ]


def test_client_from_args_loads_config_and_passes_smith_config(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    fake_config = object()

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(handlers, "SmithClient", _FakeClient)
    monkeypatch.setattr(handlers, "load_config", lambda: fake_config)

    handlers._client_from_args(SimpleNamespace())

    assert captured == {"smith_config": fake_config}
