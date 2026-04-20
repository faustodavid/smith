#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from typing import Any

from smith.client import SmithClient
from smith.config import RemoteConfig, SmithConfig, _default_config_path, load_config, save_config
from smith.formatting import dumps_json, make_envelope, render_text

EXIT_OK = 0
EXIT_INVALID_ARGS = 2
EXIT_AUTH_FAILURE = 3
EXIT_API_FAILURE = 4
EXIT_PARTIAL = 5


def _csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _selected_remote(args: argparse.Namespace) -> str:
    remote = getattr(args, "remote", None)
    return str(remote).strip() if remote is not None else ""


def _selected_remote_provider(args: argparse.Namespace) -> str:
    provider = str(getattr(args, "remote_provider", "") or "").strip().lower()
    if provider:
        return provider

    remote_name = _selected_remote(args)
    if not remote_name or remote_name == "all":
        return ""

    try:
        config = load_config()
    except Exception:
        return ""

    remote = config.remotes.get(remote_name)
    if not remote:
        return ""
    return remote.provider


def _selected_target(args: argparse.Namespace) -> str:
    remote = _selected_remote(args)
    if remote:
        return remote
    return _selected_remote_provider(args)


def _is_full_gitlab_repo_path(value: str) -> bool:
    normalized = str(value or "").strip().strip("/")
    return "/" in normalized


def _is_partial_result(data: Any) -> bool:
    if isinstance(data, dict) and "remotes" in data:
        remotes = data.get("remotes", {})
        if not isinstance(remotes, dict):
            return False
        for entry in remotes.values():
            if not isinstance(entry, dict):
                continue
            if not bool(entry.get("ok", False)):
                return True
            if bool(entry.get("partial", False)):
                return True
            warnings = entry.get("warnings")
            if isinstance(warnings, list) and warnings:
                return True
        return False
    if isinstance(data, dict):
        if bool(data.get("partial", False)):
            return True
        warnings = data.get("warnings")
        if isinstance(warnings, list) and warnings:
            return True
    return False


def _command_meta(
    args: argparse.Namespace,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del args
    return dict(meta or {})


def validate_args_for_remote(args: argparse.Namespace) -> None:
    command = str(getattr(args, "command_id", ""))
    remote = _selected_remote(args).lower()
    provider = _selected_remote_provider(args)
    repo_filters = getattr(args, "repos", None)
    has_repo_filters = isinstance(repo_filters, list) and any(str(item or "").strip() for item in repo_filters)
    has_project_filter = bool(str(getattr(args, "project", "") or "").strip())
    has_repo_arg = bool(str(getattr(args, "repo", "") or "").strip())
    pr_statuses = getattr(args, "status", None)

    if command == "code.search" and not str(getattr(args, "query", "") or "").strip():
        raise ValueError('code search requires a query. Example: smith code search "grafana.*"')
    if command == "code.search" and remote == "all" and has_project_filter:
        raise ValueError("`smith code search` searches all remotes and does not support `--project`. Use `smith <remote> code search`.")
    if command == "code.search" and remote == "all" and has_repo_filters:
        raise ValueError("`smith code search` searches all remotes and does not support `--repo`. Use `smith <remote> code search`.")
    if command == "prs.search" and not str(getattr(args, "query", "") or "").strip():
        raise ValueError('prs search requires a query. Example: smith prs search "grafana rollout"')
    if command == "prs.search" and remote == "all" and has_project_filter:
        raise ValueError("`smith prs search` searches all remotes and does not support `--project`. Use `smith <remote> prs search`.")
    if command == "prs.search" and remote == "all" and (has_repo_filters or has_repo_arg):
        raise ValueError("`smith prs search` searches all remotes and does not support `--repo`. Use `smith <remote> prs search`.")
    if command in {"prs.list", "prs.search"} and pr_statuses:
        allowed_pr_statuses = {"active", "completed", "abandoned"}
        for status in pr_statuses:
            if str(status).strip().lower() not in allowed_pr_statuses:
                raise ValueError("status must be one of: active, completed, abandoned")

    if not remote:
        return

    if command not in {"code.search", "prs.search"} and remote == "all":
        raise ValueError(f"{command} does not support remote 'all'. Use a configured remote name.")

    if command == "code.search" and provider == "github" and str(getattr(args, "project", "") or "").strip():
        raise ValueError("GitHub code search does not support `--project`. Use `--repo` instead.")
    if command == "code.search" and provider == "gitlab" and str(getattr(args, "project", "") or "").strip():
        raise ValueError("GitLab code search does not support `--project`. Use `--repo` instead.")
    if command == "prs.search" and provider == "github" and str(getattr(args, "project", "") or "").strip():
        raise ValueError("GitHub PR search does not support `--project`. Use `--repo` instead.")
    if command == "prs.search" and provider == "gitlab" and str(getattr(args, "project", "") or "").strip():
        raise ValueError("GitLab PR search does not support `--project`. Use `--repo` instead.")
    if provider == "gitlab":
        repo = str(getattr(args, "repo", "") or "").strip()
        if repo and not _is_full_gitlab_repo_path(repo):
            raise ValueError("GitLab repositories must use full `group/project` paths.")

        if isinstance(repo_filters, list):
            for repo_filter in repo_filters:
                if str(repo_filter or "").strip() and not _is_full_gitlab_repo_path(str(repo_filter)):
                    raise ValueError("GitLab repositories must use full `group/project` paths.")


def _emit_success(
    *,
    args: argparse.Namespace,
    command: str,
    data: Any,
    meta: dict[str, Any] | None = None,
    partial: bool = False,
) -> int:
    payload_meta = _command_meta(args, meta)
    if args.output_format == "json":
        payload = make_envelope(ok=True, command=command, data=data, meta=payload_meta, error=None)
        print(dumps_json(payload))
    else:
        print(render_text(command, data))

    if partial:
        return EXIT_PARTIAL
    return EXIT_OK


def _emit_error(
    *,
    args: argparse.Namespace,
    command: str,
    code: str,
    message: str,
    exit_code: int,
) -> int:
    payload_meta = _command_meta(args)
    if args.output_format == "json":
        payload = make_envelope(
            ok=False,
            command=command,
            data=None,
            meta=payload_meta,
            error={"code": code, "message": message},
        )
        print(dumps_json(payload))
    else:
        print(message, file=sys.stderr)
    return exit_code


def _client_from_args(args: argparse.Namespace) -> SmithClient:
    del args
    return SmithClient()


def _remote_summary(remote: RemoteConfig) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "name": remote.name,
        "provider": remote.provider,
        "enabled": remote.enabled,
    }
    if remote.org:
        summary["org"] = remote.org
    if remote.host:
        summary["host"] = remote.host
    return summary


def handle_config_list(client: SmithClient | None, args: argparse.Namespace) -> int:
    del client
    config = load_config()
    remotes_list = [_remote_summary(remote) for remote in config.remotes.values()]
    return _emit_success(
        args=args,
        command=args.command_id,
        data={"remotes": remotes_list},
        partial=False,
    )


def handle_config_show(client: SmithClient | None, args: argparse.Namespace) -> int:
    del client
    config = load_config()
    remote_name = args.remote_name
    remote = config.remotes.get(remote_name)
    if not remote:
        return _emit_error(
            args=args,
            command=args.command_id,
            code="not_found",
            message=f"Remote '{remote_name}' not found",
            exit_code=EXIT_INVALID_ARGS,
        )
    data: dict[str, Any] = {
        "name": remote.name,
        "provider": remote.provider,
        "enabled": remote.enabled,
    }
    if remote.org:
        data["org"] = remote.org
    if remote.host:
        data["host"] = remote.host
    if remote.token_env:
        data["token_env"] = remote.token_env
    if remote.api_url:
        data["api_url"] = remote.api_url
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=False,
    )


def handle_config_init(client: SmithClient | None, args: argparse.Namespace) -> int:
    del client
    path = _default_config_path()
    if path.exists():
        return _emit_error(
            args=args,
            command=args.command_id,
            code="already_exists",
            message=f"Config file already exists at {path}",
            exit_code=EXIT_INVALID_ARGS,
        )

    if args.output_format == "json":
        config = SmithConfig(remotes={}, defaults={})
        save_config(config, config_path=path)
        return _emit_success(
            args=args,
            command=args.command_id,
            data={"path": str(path), "remotes_count": 0},
            partial=False,
        )

    path.parent.mkdir(parents=True, exist_ok=True)

    from smith.cli.onboarding import (
        _print_manual_setup_instructions,
        run_interactive_init,
    )

    print("Welcome to Smith!")
    print()
    print("How would you like to configure your remotes?")
    print("  1) Interactive setup")
    print("  2) Manual setup (edit config.yaml yourself)")

    while True:
        try:
            raw = input("Choice [1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(1)
        if not raw or raw == "1":
            config = run_interactive_init(config_path=path)
            return _emit_success(
                args=args,
                command=args.command_id,
                data={"path": str(path), "remotes_count": len(config.remotes)},
                partial=False,
            )
        if raw == "2":
            config = SmithConfig(remotes={}, defaults={})
            save_config(config, config_path=path)
            _print_manual_setup_instructions(path)
            return _emit_success(
                args=args,
                command=args.command_id,
                data={"path": str(path), "remotes_count": 0},
                partial=False,
            )
        print("  Enter 1 or 2.")


def handle_config_edit(client: SmithClient | None, args: argparse.Namespace) -> int:
    del client
    config = load_config()
    path = _default_config_path()

    from smith.cli.onboarding import run_interactive_edit

    updated = run_interactive_edit(config, config_path=path)
    return _emit_success(
        args=args,
        command=args.command_id,
        data={"path": str(path), "remotes_count": len(updated.remotes)},
        partial=False,
    )


def handle_config_path(client: SmithClient | None, args: argparse.Namespace) -> int:
    del client
    path = _default_config_path()
    return _emit_success(
        args=args,
        command=args.command_id,
        data={"path": str(path), "exists": path.exists()},
        partial=False,
    )


def handle_config_enable(client: SmithClient | None, args: argparse.Namespace) -> int:
    del client
    config = load_config()
    remote_name = args.remote_name
    remote = config.remotes.get(remote_name)
    if not remote:
        return _emit_error(
            args=args,
            command=args.command_id,
            code="not_found",
            message=f"Remote '{remote_name}' not found",
            exit_code=EXIT_INVALID_ARGS,
        )

    if remote.enabled:
        return _emit_success(
            args=args,
            command=args.command_id,
            data={"message": f"Remote '{remote_name}' is already enabled"},
            partial=False,
        )

    updated_remote = replace(remote, enabled=True)
    updated_remotes = dict(config.remotes)
    updated_remotes[remote_name] = updated_remote
    updated_config = SmithConfig(remotes=updated_remotes, defaults=config.defaults)
    save_config(updated_config)

    return _emit_success(
        args=args,
        command=args.command_id,
        data={"message": f"Remote '{remote_name}' enabled"},
        partial=False,
    )


def handle_config_disable(client: SmithClient | None, args: argparse.Namespace) -> int:
    del client
    config = load_config()
    remote_name = args.remote_name
    remote = config.remotes.get(remote_name)
    if not remote:
        return _emit_error(
            args=args,
            command=args.command_id,
            code="not_found",
            message=f"Remote '{remote_name}' not found",
            exit_code=EXIT_INVALID_ARGS,
        )

    if not remote.enabled:
        return _emit_success(
            args=args,
            command=args.command_id,
            data={"message": f"Remote '{remote_name}' is already disabled"},
            partial=False,
        )

    updated_remote = replace(remote, enabled=False)
    updated_remotes = dict(config.remotes)
    updated_remotes[remote_name] = updated_remote
    updated_config = SmithConfig(remotes=updated_remotes, defaults=config.defaults)
    save_config(updated_config)

    return _emit_success(
        args=args,
        command=args.command_id,
        data={"message": f"Remote '{remote_name}' disabled"},
        partial=False,
    )


def handle_cache_clean(client: SmithClient | None, args: argparse.Namespace) -> int:
    del client

    data = SmithClient.execute_cache_clean(remote=getattr(args, "cache_remote", "all"))
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=False,
    )


def handle_discover_projects(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_discover_projects(remote_or_provider=_selected_target(args))
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_discover_repos(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_discover_repos(
        remote_or_provider=_selected_target(args),
        project=getattr(args, "project", None),
        group=getattr(args, "group", None),
        grep=getattr(args, "grep", None),
        skip=getattr(args, "skip", 0),
        take=getattr(args, "take", None),
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_list_groups(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_list_groups(
        remote_or_provider=_selected_target(args),
        grep=getattr(args, "grep", None),
        skip=getattr(args, "skip", 0),
        take=getattr(args, "take", None),
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_code_search(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_code_search(
        remote_or_provider=_selected_target(args),
        query=args.query,
        project=args.project,
        repos=args.repos,
        skip=args.skip,
        take=args.take,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_code_grep(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_code_grep(
        remote_or_provider=_selected_target(args),
        project=getattr(args, "project", None),
        repo=getattr(args, "repo"),
        pattern=args.pattern,
        path=args.path,
        branch=args.branch,
        glob=args.glob,
        output_mode=args.output_mode,
        case_insensitive=not args.case_sensitive,
        context_lines=args.context_lines,
        from_line=args.from_line,
        to_line=args.to_line,
        reverse=getattr(args, "reverse", False),
        no_clone=args.no_clone,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_pr_list(client: SmithClient, args: argparse.Namespace) -> int:
    if _selected_remote_provider(args) == "azdo":
        projects = [args.project]
        repos = [args.repo]
    else:
        projects = None
        repos = [args.repo]

    data = client.execute_pr_list(
        remote_or_provider=_selected_target(args),
        projects=projects,
        repos=repos,
        statuses=args.status,
        creators=args.creator,
        date_from=args.date_from,
        date_to=args.date_to,
        skip=args.skip,
        take=args.take,
        exclude_drafts=args.exclude_drafts,
        include_labels=args.include_labels,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_pr_search(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_pr_search(
        remote_or_provider=_selected_target(args),
        query=args.query,
        project=getattr(args, "project", None),
        repos=getattr(args, "repos", None),
        statuses=args.status,
        creators=args.creator,
        date_from=args.date_from,
        date_to=args.date_to,
        skip=args.skip,
        take=args.take,
        exclude_drafts=args.exclude_drafts,
        include_labels=args.include_labels,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_pr_get(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_pr_get(
        remote_or_provider=_selected_target(args),
        project=getattr(args, "project", None),
        repo=args.repo,
        pull_request_id=args.id,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_pr_threads(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_pr_threads(
        remote_or_provider=_selected_target(args),
        project=getattr(args, "project", None),
        repo=args.repo,
        pull_request_id=args.id,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_ci_list(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_ci_list(
        remote_or_provider=_selected_target(args),
        project=getattr(args, "project", None),
        repo=getattr(args, "repo", None),
        pipeline_id=args.id,
        grep=getattr(args, "grep", None),
        statuses=getattr(args, "status", None),
        skip=getattr(args, "skip", 0),
        take=getattr(args, "take", None),
        max_depth=getattr(args, "max_depth", 0),
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_ci_logs(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_ci_logs(
        remote_or_provider=_selected_target(args),
        project=getattr(args, "project", None),
        repo=getattr(args, "repo", None),
        build_id=args.id,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_ci_grep(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_ci_grep(
        remote_or_provider=_selected_target(args),
        project=getattr(args, "project", None),
        repo=getattr(args, "repo", None),
        build_id=args.id,
        log_id=args.log_id,
        pattern=args.pattern,
        output_mode=args.output_mode,
        case_insensitive=not args.case_sensitive,
        context_lines=args.context_lines,
        from_line=args.from_line,
        to_line=args.to_line,
        reverse=getattr(args, "reverse", False),
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_work_get(client: SmithClient, args: argparse.Namespace) -> int:
    request_kwargs: dict[str, Any] = {
        "remote_or_provider": _selected_target(args),
        "project": getattr(args, "project", None),
        "repo": getattr(args, "repo", None),
        "work_item_id": args.id,
    }
    data = client.execute_work_get(
        **request_kwargs,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_work_search(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_work_search(
        remote_or_provider=_selected_target(args),
        query=args.query,
        project=getattr(args, "project", None),
        repo=getattr(args, "repo", None),
        area=getattr(args, "area", None),
        work_item_type=args.type,
        state=args.state,
        assigned_to=args.assigned_to,
        skip=args.skip,
        take=args.take,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_work_mine(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_work_mine(
        remote_or_provider=_selected_target(args),
        project=getattr(args, "project", None),
        repo=getattr(args, "repo", None),
        include_closed=args.include_closed,
        skip=args.skip,
        take=args.take,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )
