#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from smith.client import SmithClient
from smith.formatting import dumps_json, make_envelope, render_text

EXIT_OK = 0
EXIT_INVALID_ARGS = 2
EXIT_AUTH_FAILURE = 3
EXIT_API_FAILURE = 4
EXIT_PARTIAL = 5


def _csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _selected_providers(provider: str) -> list[str]:
    normalized = (provider or "azdo").strip().lower()
    if normalized == "all":
        return ["github", "azdo"]
    return [normalized]


def _requires_github_org(provider: str) -> bool:
    return "github" in _selected_providers(provider)


def _is_partial_result(data: Any) -> bool:
    if isinstance(data, dict) and "providers" in data:
        providers = data.get("providers", {})
        if not isinstance(providers, dict):
            return False
        for entry in providers.values():
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


def validate_args_for_provider(args: argparse.Namespace) -> None:
    command = str(getattr(args, "command_id", ""))
    provider = str(getattr(args, "provider", "") or "").strip().lower()

    if not provider:
        return

    if command != "code.search" and provider == "all":
        raise ValueError(f"{command} does not support provider 'all'. Use azdo or github.")

    if command == "code.search" and not str(getattr(args, "query", "") or "").strip():
        raise ValueError("code search requires a query. Example: smith code search \"grafana.*\"")

    if _requires_github_org(provider):
        if not os.getenv("GITHUB_ORG", "").strip():
            raise ValueError("Missing GITHUB_ORG. Example: export GITHUB_ORG=<org>")

    if command == "board.list" and provider == "github":
        raise ValueError("GitHub does not support `board list`. Use `board search` instead.")


def _emit_success(
    *,
    args: argparse.Namespace,
    command: str,
    data: Any,
    meta: dict[str, Any] | None = None,
    partial: bool = False,
) -> int:
    if args.output_format == "json":
        payload = make_envelope(ok=True, command=command, data=data, meta=meta or {}, error=None)
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
    if args.output_format == "json":
        payload = make_envelope(
            ok=False,
            command=command,
            data=None,
            meta={},
            error={"code": code, "message": message},
        )
        print(dumps_json(payload))
    else:
        print(message, file=sys.stderr)
    return exit_code


def _client_from_args(args: argparse.Namespace) -> SmithClient:
    return SmithClient(org_url=args.org_url)


def handle_projects_list(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_projects_list(provider=args.provider)
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_repos_list(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_repos_list(provider=args.provider, project=getattr(args, "project", None))
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_code_search(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_code_search(
        provider=args.provider,
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
        provider=args.provider,
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
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_pr_list(client: SmithClient, args: argparse.Namespace) -> int:
    if args.provider == "azdo":
        projects = [args.project]
        repos = [args.repo]
    else:
        projects = None
        repos = [args.repo]

    data = client.execute_pr_list(
        provider=args.provider,
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


def handle_pr_get(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_pr_get(
        provider=args.provider,
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
        provider=args.provider,
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


def handle_build_logs(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_build_logs(
        provider=args.provider,
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


def handle_build_grep(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_build_grep(
        provider=args.provider,
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
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_board_ticket(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_board_ticket(
        provider=args.provider,
        project=getattr(args, "project", None),
        repo=getattr(args, "repo", None),
        work_item_id=args.id,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_board_list(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_board_list(
        provider=args.provider,
        project=getattr(args, "project", None),
        wiql=args.wiql,
        skip=args.skip,
        take=args.take,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_board_search(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_board_search(
        provider=args.provider,
        query=args.query,
        project=getattr(args, "project", None),
        repo=getattr(args, "repo", None),
        area=args.area,
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


def handle_board_mine(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_board_mine(
        provider=args.provider,
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
