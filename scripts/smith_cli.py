#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from typing import Any, Callable

from smith_client import (
    ThanosLocalApiError,
    ThanosLocalAuthError,
    ThanosLocalClient,
)
from smith_format import dumps_json, make_envelope, render_text

EXIT_OK = 0
EXIT_INVALID_ARGS = 2
EXIT_AUTH_FAILURE = 3
EXIT_API_FAILURE = 4
EXIT_PARTIAL = 5


def _csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


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


def _client_from_args(args: argparse.Namespace) -> ThanosLocalClient:
    return ThanosLocalClient(org_url=args.org_url)


def handle_projects_list(client: ThanosLocalClient, args: argparse.Namespace) -> int:
    data = client.list_projects()
    return _emit_success(args=args, command=args.command_id, data=data)


def handle_repos_list(client: ThanosLocalClient, args: argparse.Namespace) -> int:
    data = client.list_repositories(project=args.project)
    return _emit_success(args=args, command=args.command_id, data=data)


def handle_code_search(client: ThanosLocalClient, args: argparse.Namespace) -> int:
    data = client.search_code(
        query=args.query,
        project=args.project,
        repos=args.repos,
        skip=args.skip,
        take=args.take,
    )
    return _emit_success(args=args, command=args.command_id, data=data)


def handle_code_grep(client: ThanosLocalClient, args: argparse.Namespace) -> int:
    data = client.grep(
        project=args.project,
        repo=args.repo,
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
        partial=bool(data.get("partial", False)),
    )


def handle_pr_list(client: ThanosLocalClient, args: argparse.Namespace) -> int:
    data = client.list_pull_requests(
        projects=args.project,
        repos=args.repo,
        statuses=args.status,
        creators=args.creator,
        date_from=args.date_from,
        date_to=args.date_to,
        skip=args.skip,
        take=args.take,
        exclude_drafts=args.exclude_drafts,
        include_labels=args.include_labels,
    )
    return _emit_success(args=args, command=args.command_id, data=data)


def handle_pr_get(client: ThanosLocalClient, args: argparse.Namespace) -> int:
    data = client.get_pull_request(
        project=args.project,
        repo=args.repo,
        pull_request_id=args.id,
    )
    return _emit_success(args=args, command=args.command_id, data=data)


def handle_pr_threads(client: ThanosLocalClient, args: argparse.Namespace) -> int:
    data = client.get_pull_request_threads(
        project=args.project,
        repo=args.repo,
        pull_request_id=args.id,
    )
    return _emit_success(args=args, command=args.command_id, data=data)


def handle_build_logs(client: ThanosLocalClient, args: argparse.Namespace) -> int:
    data = client.get_build_log(project=args.project, build_id=args.id)
    return _emit_success(args=args, command=args.command_id, data=data)


def handle_build_grep(client: ThanosLocalClient, args: argparse.Namespace) -> int:
    data = client.grep_build_log(
        project=args.project,
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
        partial=bool(data.get("partial", False)),
    )


def handle_board_ticket(client: ThanosLocalClient, args: argparse.Namespace) -> int:
    data = client.get_ticket_by_id(project=args.project, work_item_id=args.id)
    return _emit_success(args=args, command=args.command_id, data=data)


def handle_board_list(client: ThanosLocalClient, args: argparse.Namespace) -> int:
    data = client.list_work_items(
        project=args.project,
        wiql=args.wiql,
        skip=args.skip,
        take=args.take,
    )
    return _emit_success(args=args, command=args.command_id, data=data)


def handle_board_search(client: ThanosLocalClient, args: argparse.Namespace) -> int:
    data = client.search_work_items(
        query=args.query,
        project=args.project,
        area=args.area,
        work_item_type=args.type,
        state=args.state,
        assigned_to=args.assigned_to,
        skip=args.skip,
        take=args.take,
    )
    return _emit_success(args=args, command=args.command_id, data=data)


def handle_board_mine(client: ThanosLocalClient, args: argparse.Namespace) -> int:
    data = client.get_my_work_items(
        project=args.project,
        include_closed=args.include_closed,
        skip=args.skip,
        take=args.take,
    )
    return _emit_success(args=args, command=args.command_id, data=data)


def _add_output_format(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )


def _set_handler(
    parser: argparse.ArgumentParser,
    handler: Callable[[ThanosLocalClient, argparse.Namespace], int],
    command_id: str,
) -> None:
    parser.set_defaults(handler=handler, command_id=command_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smith",
        description="Thanos-style Azure DevOps read-only workflows with local credentials.",
    )
    parser.add_argument(
        "--org-url",
        dest="org_url",
        default=None,
        help="Override AZURE_DEVOPS_ORG_URL for this invocation.",
    )

    root_subparsers = parser.add_subparsers(dest="group")

    projects = root_subparsers.add_parser("projects", help="Project discovery commands")
    projects_sub = projects.add_subparsers(dest="action")
    projects_list = projects_sub.add_parser("list", help="List Azure DevOps projects")
    _add_output_format(projects_list)
    _set_handler(projects_list, handle_projects_list, "projects.list")

    repos = root_subparsers.add_parser("repos", help="Repository discovery commands")
    repos_sub = repos.add_subparsers(dest="action")
    repos_list = repos_sub.add_parser("list", help="List repositories for a project")
    repos_list.add_argument("--project", required=True, help="Azure DevOps project name")
    _add_output_format(repos_list)
    _set_handler(repos_list, handle_repos_list, "repos.list")

    code = root_subparsers.add_parser("code", help="Code search and grep commands")
    code_sub = code.add_subparsers(dest="action")

    code_search = code_sub.add_parser("search", help="Broad Azure DevOps code search")
    code_search.add_argument("--query", required=True, help="Search query text")
    code_search.add_argument("--project", help="Project filter")
    code_search.add_argument("--repos", type=_csv_list, help="Comma-separated repository names")
    code_search.add_argument("--skip", type=int, default=0, help="Results offset")
    code_search.add_argument("--take", type=int, default=20, help="Results count")
    _add_output_format(code_search)
    _set_handler(code_search, handle_code_search, "code.search")

    code_grep = code_sub.add_parser("grep", help="Targeted grep in a specific repository")
    code_grep.add_argument("--project", required=True, help="Azure DevOps project name")
    code_grep.add_argument("--repo", required=True, help="Repository name")
    code_grep.add_argument("--pattern", help="Regex pattern (default: match all)")
    code_grep.add_argument("--path", help="Path scope (default: /)")
    code_grep.add_argument("--branch", help="Branch name")
    code_grep.add_argument("--glob", help="Filename glob filter (e.g. *.tf)")
    code_grep.add_argument(
        "--output-mode",
        choices=["content", "files_with_matches", "count"],
        default="content",
    )
    code_grep.add_argument("--context-lines", type=int, default=3)
    code_grep.add_argument("--from-line", type=int)
    code_grep.add_argument("--to-line", type=int)
    code_grep.add_argument("--case-sensitive", action="store_true")
    _add_output_format(code_grep)
    _set_handler(code_grep, handle_code_grep, "code.grep")

    pr = root_subparsers.add_parser("pr", help="Pull request read commands")
    pr_sub = pr.add_subparsers(dest="action")

    pr_list = pr_sub.add_parser("list", help="List pull requests")
    pr_list.add_argument("--project", type=_csv_list, help="Comma-separated project names")
    pr_list.add_argument("--repo", type=_csv_list, help="Comma-separated repo names or IDs")
    pr_list.add_argument(
        "--status",
        type=_csv_list,
        help="Comma-separated statuses: active,completed,abandoned",
    )
    pr_list.add_argument("--creator", type=_csv_list, help="Comma-separated creator email/name filters")
    pr_list.add_argument("--date-from", help="ISO date/datetime lower bound")
    pr_list.add_argument("--date-to", help="ISO date/datetime upper bound")
    pr_list.add_argument("--skip", type=int, default=0)
    pr_list.add_argument("--take", type=int, default=100)
    pr_list.add_argument("--exclude-drafts", action="store_true")
    pr_list.add_argument("--include-labels", action="store_true")
    _add_output_format(pr_list)
    _set_handler(pr_list, handle_pr_list, "pr.list")

    pr_get = pr_sub.add_parser("get", help="Get pull request details")
    pr_get.add_argument("--project", required=True)
    pr_get.add_argument("--repo", required=True)
    pr_get.add_argument("--id", required=True, type=int)
    _add_output_format(pr_get)
    _set_handler(pr_get, handle_pr_get, "pr.get")

    pr_threads = pr_sub.add_parser("threads", help="Get pull request comment threads")
    pr_threads.add_argument("--project", required=True)
    pr_threads.add_argument("--repo", required=True)
    pr_threads.add_argument("--id", required=True, type=int)
    _add_output_format(pr_threads)
    _set_handler(pr_threads, handle_pr_threads, "pr.threads")

    build = root_subparsers.add_parser("build", help="Build log read commands")
    build_sub = build.add_subparsers(dest="action")

    build_logs = build_sub.add_parser("logs", help="List logs for a build")
    build_logs.add_argument("--project", required=True)
    build_logs.add_argument("--id", required=True, type=int, help="Build ID")
    _add_output_format(build_logs)
    _set_handler(build_logs, handle_build_logs, "build.logs")

    build_grep = build_sub.add_parser("grep", help="Search or read build logs")
    build_grep.add_argument("--project", required=True)
    build_grep.add_argument("--id", required=True, type=int, help="Build ID")
    build_grep.add_argument("--log-id", type=int)
    build_grep.add_argument("--pattern")
    build_grep.add_argument(
        "--output-mode",
        choices=["content", "logs_with_matches", "count"],
        default="content",
    )
    build_grep.add_argument("--context-lines", type=int, default=3)
    build_grep.add_argument("--from-line", type=int)
    build_grep.add_argument("--to-line", type=int)
    build_grep.add_argument("--case-sensitive", action="store_true")
    _add_output_format(build_grep)
    _set_handler(build_grep, handle_build_grep, "build.grep")

    board = root_subparsers.add_parser("board", help="Board read commands")
    board_sub = board.add_subparsers(dest="action")

    board_ticket = board_sub.add_parser("ticket", help="Get work item by ID")
    board_ticket.add_argument("--project", required=True)
    board_ticket.add_argument("--id", required=True, type=int)
    _add_output_format(board_ticket)
    _set_handler(board_ticket, handle_board_ticket, "board.ticket")

    board_list = board_sub.add_parser("list", help="Run WIQL query")
    board_list.add_argument("--project", required=True)
    board_list.add_argument("--wiql", required=True)
    board_list.add_argument("--skip", type=int, default=0)
    board_list.add_argument("--take", type=int, default=20)
    _add_output_format(board_list)
    _set_handler(board_list, handle_board_list, "board.list")

    board_search = board_sub.add_parser("search", help="Search work items")
    board_search.add_argument("--query", required=True)
    board_search.add_argument("--project")
    board_search.add_argument("--area")
    board_search.add_argument("--type")
    board_search.add_argument("--state")
    board_search.add_argument("--assigned-to")
    board_search.add_argument("--skip", type=int, default=0)
    board_search.add_argument("--take", type=int, default=20)
    _add_output_format(board_search)
    _set_handler(board_search, handle_board_search, "board.search")

    board_mine = board_sub.add_parser("mine", help="Get my assigned work items")
    board_mine.add_argument("--project")
    board_mine.add_argument("--include-closed", action="store_true")
    board_mine.add_argument("--skip", type=int, default=0)
    board_mine.add_argument("--take", type=int, default=20)
    _add_output_format(board_mine)
    _set_handler(board_mine, handle_board_mine, "board.mine")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return EXIT_INVALID_ARGS

    command = getattr(args, "command_id", "unknown")

    try:
        client = _client_from_args(args)
        return handler(client, args)
    except ValueError as exc:
        return _emit_error(
            args=args,
            command=command,
            code="invalid_args",
            message=str(exc),
            exit_code=EXIT_INVALID_ARGS,
        )
    except ThanosLocalAuthError as exc:
        return _emit_error(
            args=args,
            command=command,
            code="auth_failure",
            message=str(exc),
            exit_code=EXIT_AUTH_FAILURE,
        )
    except ThanosLocalApiError as exc:
        return _emit_error(
            args=args,
            command=command,
            code="api_error",
            message=str(exc),
            exit_code=EXIT_API_FAILURE,
        )
    except Exception as exc:  # pragma: no cover - defensive fallback
        return _emit_error(
            args=args,
            command=command,
            code="unexpected_error",
            message=f"Unexpected error: {exc}",
            exit_code=EXIT_API_FAILURE,
        )


if __name__ == "__main__":
    raise SystemExit(main())
