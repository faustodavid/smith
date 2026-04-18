from __future__ import annotations

import json
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
        "remote_provider": "azdo",
        "command_id": "orgs",
        "primary_path": "orgs",
        "alias_used": None,
        "removed_flags": [],
        "query": "grafana",
        "github_org": None,
        "azdo_org": None,
        "project": "proj-a",
        "group": None,
        "grep": None,
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
        "cache_remote": "all",
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


def _make_remote_config(
    name: str,
    provider: str,
    *,
    enabled: bool = True,
    org: str | None = None,
    host: str | None = None,
    token_env: str | None = None,
    api_url: str | None = None,
) -> RemoteConfig:
    default_org = {"github": "octo-org", "gitlab": "", "azdo": "acme", "youtrack": ""}[provider] if org is None else org
    default_host = {
        "github": "github.com",
        "gitlab": "gitlab.com",
        "azdo": "dev.azure.com",
        "youtrack": "youtrack.example.test",
    }[provider] if host is None else host
    default_token_env = {
        "github": "GITHUB_TOKEN",
        "gitlab": "GITLAB_TOKEN",
        "azdo": "AZURE_DEVOPS_PAT",
        "youtrack": "YOUTRACK_TOKEN",
    }[provider] if token_env is None else token_env
    default_api_url = {
        "github": "https://api.github.com",
        "gitlab": "https://gitlab.com/api/v4",
        "azdo": "https://dev.azure.com",
        "youtrack": "https://youtrack.example.test/api",
    }[provider] if api_url is None else api_url
    return RemoteConfig(
        name=name,
        provider=provider,
        org=default_org,
        host=default_host,
        token_env=default_token_env,
        enabled=enabled,
        api_url=default_api_url,
    )


def test_csv_list_and_remote_helpers() -> None:
    args = _make_args(remote="github", remote_provider="github")

    assert handlers._csv_list(" a, ,b , c ") == ["a", "b", "c"]
    assert handlers._selected_remote(args) == "github"
    assert handlers._selected_remote_provider(args) == "github"


def test_is_partial_result_detects_grouped_and_flat_payloads() -> None:
    grouped_warning = {
        "remotes": {
            "github": {
                "ok": True,
                "warnings": ["rate limited"],
                "partial": False,
            }
        }
    }
    grouped_failure = {"remotes": {"azdo": {"ok": False, "warnings": [], "partial": False}}}
    flat_warning = {"warnings": ["warning"]}
    flat_partial = {"partial": True}

    assert handlers._is_partial_result(grouped_warning) is True
    assert handlers._is_partial_result(grouped_failure) is True
    assert handlers._is_partial_result(flat_warning) is True
    assert handlers._is_partial_result(flat_partial) is True
    assert handlers._is_partial_result({"remotes": []}) is False
    assert handlers._is_partial_result({"warnings": []}) is False


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (_make_args(command_id="repos", remote="all", remote_provider=""), "does not support remote 'all'"),
        (_make_args(command_id="code.search", query="   "), "code search requires a query"),
        (_make_args(command_id="code.search", remote="all", remote_provider="", project="proj-a", repos=None),
         "does not support `--project`"),
        (_make_args(command_id="code.search", remote="all", remote_provider="", project=None, repos=["repo-a"]),
         "does not support `--repo`"),
        (_make_args(command_id="prs.search", query="   ", repo=None, repos=None), "prs search requires a query"),
        (_make_args(command_id="prs.search", remote="all", remote_provider="", project="proj-a", repo=None, repos=None),
         "does not support `--project`"),
        (_make_args(command_id="prs.search", remote="all", remote_provider="", project=None, repo=None, repos=["repo-a"]),
         "does not support `--repo`"),
        (_make_args(command_id="prs.search", remote="all", remote_provider="", project=None, status=["stale"], repo=None, repos=None),
         "status must be one of"),
        (_make_args(command_id="code.search", remote="github", remote_provider="github", project="proj-a"),
         "GitHub code search does not support `--project`"),
        (_make_args(command_id="code.search", remote="gitlab", remote_provider="gitlab", project="proj-a"),
         "GitLab code search does not support `--project`"),
        (_make_args(command_id="code.search", remote="gitlab", remote_provider="gitlab", project=None, repos=["repo-a"]),
         "GitLab repositories must use full `group/project` paths"),
        (_make_args(command_id="code.grep", remote="gitlab", remote_provider="gitlab", project=None, repo="repo-a"),
         "GitLab repositories must use full `group/project` paths"),
        (_make_args(command_id="prs.search", remote="gitlab", remote_provider="gitlab", project=None, repo=None, repos=["repo-a"]),
         "GitLab repositories must use full `group/project` paths"),
    ],
)
def test_validate_args_for_remote_rejects_invalid_inputs(
    args: Namespace,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        handlers.validate_args_for_remote(args)


def test_validate_args_for_remote_allows_code_search_all() -> None:
    args = _make_args(command_id="code.search", remote="all", remote_provider="", project=None, repos=None)

    handlers.validate_args_for_remote(args)


def test_validate_args_for_remote_allows_prs_search_all() -> None:
    args = _make_args(
        command_id="prs.search",
        remote="all",
        remote_provider="",
        project=None,
        repo=None,
        repos=None,
    )

    handlers.validate_args_for_remote(args)


def test_validate_args_for_remote_allows_full_gitlab_repo_paths() -> None:
    args = _make_args(
        command_id="code.search",
        remote="gitlab",
        remote_provider="gitlab",
        project=None,
        repos=["engineering-tools/coderabbit"],
        repo="engineering-tools/coderabbit",
    )

    handlers.validate_args_for_remote(args)


def test_validate_args_for_remote_resolves_remote_type_from_config(monkeypatch: Any) -> None:
    args = _make_args(command_id="code.search", remote="gitlab-infra", remote_provider="", project="proj-a")
    monkeypatch.setattr(
        handlers,
        "load_config",
        lambda: SmithConfig(
            remotes={
                "gitlab-infra": RemoteConfig(
                    name="gitlab-infra",
                    provider="gitlab",
                    org="example-org",
                    host="gitlab.example.com",
                    token_env="GITLAB_INFRA_READ_ONLY_TOKEN",
                    enabled=True,
                    api_url="https://gitlab.example.com/api/v4",
                )
            },
            defaults={},
        ),
    )

    with pytest.raises(ValueError, match="GitLab code search does not support `--project`"):
        handlers.validate_args_for_remote(args)


def test_selected_remote_provider_returns_empty_when_config_lookup_cannot_resolve(monkeypatch: Any) -> None:
    assert handlers._selected_remote_provider(_make_args(remote="all", remote_provider="")) == ""

    args = _make_args(remote="gitlab-infra", remote_provider="")

    def _raise_runtime_error() -> SmithConfig:
        raise RuntimeError("boom")

    monkeypatch.setattr(handlers, "load_config", _raise_runtime_error)
    assert handlers._selected_remote_provider(args) == ""

    monkeypatch.setattr(handlers, "load_config", lambda: SmithConfig(remotes={}, defaults={}))
    assert handlers._selected_remote_provider(args) == ""


def test_handle_code_grep_uses_named_remote(monkeypatch: Any, capsys: Any) -> None:
    client = _RecordingClient(payload={"marker": "code-grep"})
    args = _make_args(command_id="code.grep", remote="gitlab-infra", remote_provider="gitlab", project=None)
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


def test_handle_config_list_returns_all_remotes_as_json(monkeypatch: Any, capsys: Any) -> None:
    github = _make_remote_config("github", "github")
    gitlab = _make_remote_config(
        "gitlab-infra",
        "gitlab",
        enabled=False,
        host="gitlab-infra.example.com",
        token_env="GITLAB_INFRA_TOKEN",
        api_url="https://gitlab-infra.example.com/api/v4",
    )
    monkeypatch.setattr(
        handlers,
        "load_config",
        lambda: SmithConfig(remotes={"github": github, "gitlab-infra": gitlab}, defaults={}),
    )
    args = _make_args(command_id="config.list", output_format="json")

    exit_code = handlers.handle_config_list(None, args)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == handlers.EXIT_OK
    assert payload["command"] == "config.list"
    assert payload["data"] == {
        "remotes": [
            {
                "name": "github",
                "provider": "github",
                "enabled": True,
                "org": "octo-org",
                "host": "github.com",
            },
            {
                "name": "gitlab-infra",
                "provider": "gitlab",
                "enabled": False,
                "host": "gitlab-infra.example.com",
            },
        ]
    }


def test_handle_config_show_returns_remote_details_as_json(monkeypatch: Any, capsys: Any) -> None:
    remote = _make_remote_config(
        "gitlab-infra",
        "gitlab",
        host="gitlab-infra.example.com",
        token_env="GITLAB_INFRA_TOKEN",
        api_url="https://gitlab-infra.example.com/api/v4",
    )
    monkeypatch.setattr(
        handlers,
        "load_config",
        lambda: SmithConfig(remotes={"gitlab-infra": remote}, defaults={}),
    )
    args = _make_args(command_id="config.show", output_format="json", remote_name="gitlab-infra")

    exit_code = handlers.handle_config_show(None, args)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == handlers.EXIT_OK
    assert payload["data"] == {
        "name": "gitlab-infra",
        "provider": "gitlab",
        "enabled": True,
        "host": "gitlab-infra.example.com",
        "token_env": "GITLAB_INFRA_TOKEN",
        "api_url": "https://gitlab-infra.example.com/api/v4",
    }


def test_handle_config_show_returns_not_found_error(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr(handlers, "load_config", lambda: SmithConfig(remotes={}, defaults={}))
    args = _make_args(command_id="config.show", output_format="json", remote_name="missing")

    exit_code = handlers.handle_config_show(None, args)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == handlers.EXIT_INVALID_ARGS
    assert payload["error"] == {"code": "not_found", "message": "Remote 'missing' not found"}


def test_handle_config_init_creates_empty_config_file(monkeypatch: Any, tmp_path: Any, capsys: Any) -> None:
    path = tmp_path / "smith-config.yaml"
    monkeypatch.setattr(handlers, "_default_config_path", lambda: path)
    args = _make_args(command_id="config.init", output_format="json")

    exit_code = handlers.handle_config_init(None, args)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == handlers.EXIT_OK
    assert payload["data"] == {"path": str(path), "remotes_count": 0}
    assert handlers.load_config(config_path=path) == SmithConfig(remotes={}, defaults={})


def test_handle_config_init_rejects_existing_config_file(monkeypatch: Any, tmp_path: Any, capsys: Any) -> None:
    path = tmp_path / "smith-config.yaml"
    path.write_text("remotes: {}\ndefaults: {}\n", encoding="utf-8")
    monkeypatch.setattr(handlers, "_default_config_path", lambda: path)
    args = _make_args(command_id="config.init", output_format="json")

    exit_code = handlers.handle_config_init(None, args)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == handlers.EXIT_INVALID_ARGS
    assert payload["error"] == {
        "code": "already_exists",
        "message": f"Config file already exists at {path}",
    }


@pytest.mark.parametrize("exists", [False, True])
def test_handle_config_path_reports_default_path_and_existence(
    monkeypatch: Any,
    tmp_path: Any,
    capsys: Any,
    exists: bool,
) -> None:
    path = tmp_path / "smith-config.yaml"
    if exists:
        path.write_text("remotes: {}\ndefaults: {}\n", encoding="utf-8")
    monkeypatch.setattr(handlers, "_default_config_path", lambda: path)
    args = _make_args(command_id="config.path", output_format="json")

    exit_code = handlers.handle_config_path(None, args)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == handlers.EXIT_OK
    assert payload["data"] == {"path": str(path), "exists": exists}


@pytest.mark.parametrize(
    ("handler_name", "command_id"),
    [
        ("handle_config_enable", "config.enable"),
        ("handle_config_disable", "config.disable"),
    ],
)
def test_handle_config_toggle_returns_not_found_for_missing_remote(
    monkeypatch: Any,
    capsys: Any,
    handler_name: str,
    command_id: str,
) -> None:
    monkeypatch.setattr(handlers, "load_config", lambda: SmithConfig(remotes={}, defaults={}))
    args = _make_args(command_id=command_id, output_format="json", remote_name="missing")

    exit_code = getattr(handlers, handler_name)(None, args)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == handlers.EXIT_INVALID_ARGS
    assert payload["error"] == {"code": "not_found", "message": "Remote 'missing' not found"}


@pytest.mark.parametrize(
    ("handler_name", "command_id", "enabled", "expected_message"),
    [
        ("handle_config_enable", "config.enable", True, "Remote 'github' is already enabled"),
        ("handle_config_disable", "config.disable", False, "Remote 'github' is already disabled"),
    ],
)
def test_handle_config_toggle_is_idempotent_when_state_already_matches(
    monkeypatch: Any,
    capsys: Any,
    handler_name: str,
    command_id: str,
    enabled: bool,
    expected_message: str,
) -> None:
    remote = _make_remote_config("github", "github", enabled=enabled)

    def _raise_save_config_assertion(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("save_config should not be called")

    monkeypatch.setattr(
        handlers,
        "load_config",
        lambda: SmithConfig(remotes={"github": remote}, defaults={"default_remote": "github"}),
    )
    monkeypatch.setattr(
        handlers,
        "save_config",
        _raise_save_config_assertion,
    )
    args = _make_args(command_id=command_id, output_format="json", remote_name="github")

    exit_code = getattr(handlers, handler_name)(None, args)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == handlers.EXIT_OK
    assert payload["data"] == {"message": expected_message}


@pytest.mark.parametrize(
    ("handler_name", "command_id", "initial_enabled", "expected_enabled", "expected_message"),
    [
        ("handle_config_enable", "config.enable", False, True, "Remote 'github' enabled"),
        ("handle_config_disable", "config.disable", True, False, "Remote 'github' disabled"),
    ],
)
def test_handle_config_toggle_persists_updated_remote_state(
    monkeypatch: Any,
    capsys: Any,
    handler_name: str,
    command_id: str,
    initial_enabled: bool,
    expected_enabled: bool,
    expected_message: str,
) -> None:
    remote = _make_remote_config("github", "github", enabled=initial_enabled)
    saved: dict[str, SmithConfig] = {}
    monkeypatch.setattr(
        handlers,
        "load_config",
        lambda: SmithConfig(remotes={"github": remote}, defaults={"default_remote": "github"}),
    )
    monkeypatch.setattr(
        handlers,
        "save_config",
        lambda config, **_kwargs: saved.setdefault("config", config),
    )
    args = _make_args(command_id=command_id, output_format="json", remote_name="github")

    exit_code = getattr(handlers, handler_name)(None, args)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == handlers.EXIT_OK
    assert payload["data"] == {"message": expected_message}
    assert saved["config"].defaults == {"default_remote": "github"}
    assert saved["config"].remotes["github"].enabled is expected_enabled


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
            _make_args(command_id="repos", grep="^proj", skip=11, take=13),
            "execute_discover_repos",
            {
                "remote_or_provider": "azdo",
                "project": "proj-a",
                "group": None,
                "grep": "^proj",
                "skip": 11,
                "take": 13,
            },
        ),
        (
            "handle_code_search",
            _make_args(command_id="code.search", remote="all", remote_provider="", project=None, repos=None),
            "execute_code_search",
            {
                "remote_or_provider": "all",
                "query": "grafana",
                "project": None,
                "repos": None,
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


def test_handle_list_groups_forwards_selected_remote(monkeypatch: Any, capsys: Any) -> None:
    client = _RecordingClient(payload={"marker": "groups"})
    args = _make_args(command_id="groups", remote="gitlab-infra", remote_provider="gitlab", grep="^platform", skip=5, take=17)
    monkeypatch.setattr(handlers, "render_text", lambda command, data: f"{command}:{data['marker']}")

    exit_code = handlers.handle_list_groups(client, args)
    output = capsys.readouterr()

    assert exit_code == handlers.EXIT_OK
    assert output.out.strip() == "groups:groups"
    assert client.calls == [(
        "execute_list_groups",
        {"remote_or_provider": "gitlab-infra", "grep": "^platform", "skip": 5, "take": 17},
    )]


def test_handle_youtrack_work_get(monkeypatch: Any, capsys: Any) -> None:
    client = _RecordingClient(payload={"marker": "youtrack-get"})
    args = _make_args(
        command_id="stories.get",
        remote="youtrack",
        remote_provider="youtrack",
        project=None,
        repo=None,
        id="RAD-1055",
    )
    monkeypatch.setattr(handlers, "render_text", lambda command, data: f"{command}:{data['marker']}")

    exit_code = handlers.handle_work_get(client, args)
    output = capsys.readouterr()

    assert exit_code == handlers.EXIT_OK
    assert output.out.strip() == "stories.get:youtrack-get"
    assert client.calls == [
        (
            "execute_work_get",
            {
                "remote_or_provider": "youtrack",
                "project": None,
                "repo": None,
                "work_item_id": "RAD-1055",
            },
        )
    ]


def test_handle_cache_clean_cleans_requested_remote_cache(monkeypatch: Any, capsys: Any, tmp_path: Any) -> None:
    github_cache = tmp_path / "github-grep"
    gitlab_cache = tmp_path / "gitlab-grep"
    github_cache.mkdir()
    gitlab_cache.mkdir()
    args = _make_args(command_id="cache.clean", output_format="text", cache_remote="all")

    monkeypatch.setenv("SMITH_GITHUB_GREP_CACHE_DIR", str(github_cache))
    monkeypatch.setenv("SMITH_GITLAB_GREP_CACHE_DIR", str(gitlab_cache))
    monkeypatch.setattr(handlers, "render_text", lambda command, data: f"{command}:{','.join(data['cleaned'])}")

    exit_code = handlers.handle_cache_clean(None, args)
    output = capsys.readouterr()

    assert exit_code == handlers.EXIT_OK
    assert output.out.strip() == f"cache.clean:{github_cache},{gitlab_cache}"
    assert not github_cache.exists()
    assert not gitlab_cache.exists()


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
    args = _make_args(command_id="prs.list", remote=provider, remote_provider=provider)
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


@pytest.mark.parametrize(
    ("provider", "project", "repos"),
    [
        ("azdo", "proj-a", ["repo-a"]),
        ("github", None, ["repo-a"]),
        ("gitlab", None, None),
    ],
)
def test_handle_pr_search_branches_by_provider(
    monkeypatch: Any,
    capsys: Any,
    provider: str,
    project: str | None,
    repos: list[str] | None,
) -> None:
    client = _RecordingClient(payload={"marker": provider})
    args = _make_args(
        command_id="prs.search",
        remote=provider,
        remote_provider=provider,
        project=project,
        repo=None,
        repos=repos,
    )
    monkeypatch.setattr(handlers, "render_text", lambda command, data: f"{command}:{data['marker']}")

    exit_code = handlers.handle_pr_search(client, args)
    output = capsys.readouterr()

    assert exit_code == handlers.EXIT_OK
    assert output.out.strip() == f"prs.search:{provider}"
    assert client.calls == [
        (
            "execute_pr_search",
            {
                "remote_or_provider": provider,
                "query": "grafana",
                "project": project,
                "repos": repos,
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


def test_handle_global_pr_search_uses_all_target(monkeypatch: Any, capsys: Any) -> None:
    client = _RecordingClient(payload={"marker": "all"})
    args = _make_args(
        command_id="prs.search",
        remote="all",
        remote_provider="",
        project=None,
        repo=None,
        repos=None,
    )
    monkeypatch.setattr(handlers, "render_text", lambda command, data: f"{command}:{data['marker']}")

    exit_code = handlers.handle_pr_search(client, args)
    output = capsys.readouterr()

    assert exit_code == handlers.EXIT_OK
    assert output.out.strip() == "prs.search:all"
    assert client.calls == [
        (
            "execute_pr_search",
            {
                "remote_or_provider": "all",
                "query": "grafana",
                "project": None,
                "repos": None,
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

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    def _raise_load_config_assertion() -> SmithConfig:
        raise AssertionError("load_config should not be called")

    monkeypatch.setattr(handlers, "SmithClient", _FakeClient)
    monkeypatch.setattr(
        handlers,
        "load_config",
        _raise_load_config_assertion,
    )

    handlers._client_from_args(SimpleNamespace())

    assert captured == {}
