#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
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


def _command_key_from_argv(argv: list[str]) -> str | None:
    if len(argv) < 2:
        return None

    group = argv[0]
    action = argv[1]

    if group == "code" and action in {"search", "grep"}:
        return f"code.{action}"
    if group == "pr" and action in {"list", "get", "threads"}:
        return f"pr.{action}"
    if group == "build" and action in {"logs", "grep"}:
        return f"build.{action}"
    if group in {"board", "stories"} and action in {"ticket", "list", "search", "mine"}:
        return f"board.{action}"
    if group == "repos" and action == "list":
        return "repos.list"
    if group == "projects" and action == "list":
        return "projects.list"
    return None


def _deprecated_syntax_message(argv: list[str]) -> str | None:
    command = _command_key_from_argv(argv)
    if command is None:
        return None

    if command == "code.search" and "--query" in argv:
        return (
            "Deprecated syntax detected for code.search. "
            "Use positional query: 'smith code search \"grafana.*\"'."
        )
    if command == "code.search":
        return None

    legacy_flags = {"--provider", "--project", "--repo"}
    if command == "code.grep" and "--pattern" in argv:
        return (
            "Deprecated syntax detected for code.grep. "
            "Use positional pattern: 'smith code grep github <repo> \"some regex\"' "
            "or 'smith code grep azdo <project> <repo> \"some regex\"'."
        )
    if not any(flag in argv for flag in legacy_flags):
        return None

    migration_examples = {
        "projects.list": "Use 'smith projects list azdo' or 'smith projects list github'.",
        "repos.list": "Use 'smith repos list azdo <project>' or 'smith repos list github'.",
        "code.grep": (
            "Use 'smith code grep github <repo> ...' or "
            "'smith code grep azdo <project> <repo> ...'."
        ),
        "pr.list": "Use 'smith pr list github <repo> ...' or 'smith pr list azdo <project> <repo> ...'.",
        "pr.get": "Use 'smith pr get github <repo> <id>' or 'smith pr get azdo <project> <repo> <id>'.",
        "pr.threads": (
            "Use 'smith pr threads github <repo> <id>' or "
            "'smith pr threads azdo <project> <repo> <id>'."
        ),
        "build.logs": "Use 'smith build logs github <repo> <id>' or 'smith build logs azdo <project> <id>'.",
        "build.grep": "Use 'smith build grep github <repo> <id> ...' or 'smith build grep azdo <project> <id> ...'.",
        "board.ticket": (
            "Use 'smith board ticket github <repo> <id>' or "
            "'smith board ticket azdo <project> <id>' (same for 'stories')."
        ),
        "board.list": (
            "Use 'smith board list azdo <project> --wiql \"...\"'; "
            "GitHub list is unsupported, use 'smith board search github <repo> --query \"...\"'."
        ),
        "board.search": (
            "Use 'smith board search github <repo> --query \"...\"' or "
            "'smith board search azdo <project> --query \"...\"' (same for 'stories')."
        ),
        "board.mine": (
            "Use 'smith board mine github <repo>' or 'smith board mine azdo <project>' "
            "(same for 'stories')."
        ),
    }

    detail = migration_examples.get(command, "Use provider-positional syntax.")
    return f"Deprecated syntax detected for {command}. {detail}"


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


def _normalize_legacy_text_args(args: argparse.Namespace) -> None:
    command = str(getattr(args, "command_id", ""))

    if command == "code.search":
        query_positional = str(getattr(args, "query", "") or "").strip()
        query_flag = str(getattr(args, "query_flag", "") or "").strip()
        if query_positional and query_flag and query_positional != query_flag:
            raise ValueError("Provide query once: either positional query or --query, not both.")
        args.query = query_positional or query_flag

    if command == "code.grep":
        pattern_positional = str(getattr(args, "pattern", "") or "").strip()
        pattern_flag = str(getattr(args, "pattern_flag", "") or "").strip()
        if pattern_positional and pattern_flag and pattern_positional != pattern_flag:
            raise ValueError("Provide pattern once: either positional pattern or --pattern, not both.")
        args.pattern = pattern_positional or pattern_flag or None


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
    data = client.execute_projects_list(provider=args.provider)
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_repos_list(client: ThanosLocalClient, args: argparse.Namespace) -> int:
    data = client.execute_repos_list(provider=args.provider, project=getattr(args, "project", None))
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_code_search(client: ThanosLocalClient, args: argparse.Namespace) -> int:
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


def handle_code_grep(client: ThanosLocalClient, args: argparse.Namespace) -> int:
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


def handle_pr_list(client: ThanosLocalClient, args: argparse.Namespace) -> int:
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


def handle_pr_get(client: ThanosLocalClient, args: argparse.Namespace) -> int:
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


def handle_pr_threads(client: ThanosLocalClient, args: argparse.Namespace) -> int:
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


def handle_build_logs(client: ThanosLocalClient, args: argparse.Namespace) -> int:
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


def handle_build_grep(client: ThanosLocalClient, args: argparse.Namespace) -> int:
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


def handle_board_ticket(client: ThanosLocalClient, args: argparse.Namespace) -> int:
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


def handle_board_list(client: ThanosLocalClient, args: argparse.Namespace) -> int:
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


def handle_board_search(client: ThanosLocalClient, args: argparse.Namespace) -> int:
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


def handle_board_mine(client: ThanosLocalClient, args: argparse.Namespace) -> int:
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


def _add_output_format(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )


def _add_search_provider_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        choices=["azdo", "github", "all"],
        default="all",
        help="Provider target (default: all)",
    )


def _set_handler(
    parser: argparse.ArgumentParser,
    handler: Callable[[ThanosLocalClient, argparse.Namespace], int],
    command_id: str,
) -> None:
    parser.set_defaults(handler=handler, command_id=command_id)


def _add_grep_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "pattern",
        nargs="?",
        help="Regex pattern (default: match all). Preferred positional form: smith code grep <provider> <scope> \"<regex>\"",
    )
    parser.add_argument("--path", help="Path scope (default: /)")
    parser.add_argument("--branch", help="Branch name")
    parser.add_argument("--glob", help="Filename glob filter (e.g. *.tf)")
    parser.add_argument(
        "--output-mode",
        choices=["content", "files_with_matches", "count"],
        default="content",
    )
    parser.add_argument("--context-lines", type=int, default=3)
    parser.add_argument("--from-line", type=int)
    parser.add_argument("--to-line", type=int)
    parser.add_argument("--case-sensitive", action="store_true")


def _add_pr_list_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--status",
        type=_csv_list,
        help="Comma-separated statuses: active,completed,abandoned",
    )
    parser.add_argument("--creator", type=_csv_list, help="Comma-separated creator email/name filters")
    parser.add_argument("--date-from", help="ISO date/datetime lower bound")
    parser.add_argument("--date-to", help="ISO date/datetime upper bound")
    parser.add_argument("--skip", type=int, default=0)
    parser.add_argument("--take", type=int, default=100)
    parser.add_argument("--exclude-drafts", action="store_true")
    parser.add_argument("--include-labels", action="store_true")


def _add_build_grep_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--log-id", type=int)
    parser.add_argument("--pattern")
    parser.add_argument(
        "--output-mode",
        choices=["content", "logs_with_matches", "count"],
        default="content",
    )
    parser.add_argument("--context-lines", type=int, default=3)
    parser.add_argument("--from-line", type=int)
    parser.add_argument("--to-line", type=int)
    parser.add_argument("--case-sensitive", action="store_true")


def _add_board_search_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--area")
    parser.add_argument("--type")
    parser.add_argument("--state")
    parser.add_argument("--assigned-to")
    parser.add_argument("--skip", type=int, default=0)
    parser.add_argument("--take", type=int, default=20)


def _add_board_group(root_subparsers: argparse._SubParsersAction[argparse.ArgumentParser], group_name: str) -> None:
    board = root_subparsers.add_parser(group_name, help="Board read commands")
    board_sub = board.add_subparsers(dest="action", required=True)

    board_ticket = board_sub.add_parser("ticket", help="Get work item/issue by ID")
    board_ticket_provider = board_ticket.add_subparsers(dest="provider", required=True)

    board_ticket_azdo = board_ticket_provider.add_parser("azdo", help="Azure DevOps board ticket")
    board_ticket_azdo.add_argument("project")
    board_ticket_azdo.add_argument("id", type=int)
    board_ticket_azdo.set_defaults(repo=None)
    _add_output_format(board_ticket_azdo)
    _set_handler(board_ticket_azdo, handle_board_ticket, "board.ticket")

    board_ticket_github = board_ticket_provider.add_parser("github", help="GitHub issue ticket")
    board_ticket_github.add_argument("repo")
    board_ticket_github.add_argument("id", type=int)
    board_ticket_github.set_defaults(project=None)
    _add_output_format(board_ticket_github)
    _set_handler(board_ticket_github, handle_board_ticket, "board.ticket")

    board_list = board_sub.add_parser("list", help="Run WIQL query (AZDO only)")
    board_list_provider = board_list.add_subparsers(dest="provider", required=True)

    board_list_azdo = board_list_provider.add_parser("azdo", help="Azure DevOps WIQL list")
    board_list_azdo.add_argument("project")
    board_list_azdo.add_argument("--wiql", required=True)
    board_list_azdo.add_argument("--skip", type=int, default=0)
    board_list_azdo.add_argument("--take", type=int, default=20)
    _add_output_format(board_list_azdo)
    _set_handler(board_list_azdo, handle_board_list, "board.list")

    board_list_github = board_list_provider.add_parser("github", help="Unsupported; use board search")
    board_list_github.add_argument("--wiql", required=True)
    board_list_github.add_argument("--skip", type=int, default=0)
    board_list_github.add_argument("--take", type=int, default=20)
    board_list_github.set_defaults(project=None)
    _add_output_format(board_list_github)
    _set_handler(board_list_github, handle_board_list, "board.list")

    board_search = board_sub.add_parser("search", help="Search work items/issues")
    board_search_provider = board_search.add_subparsers(dest="provider", required=True)

    board_search_azdo = board_search_provider.add_parser("azdo", help="Azure DevOps work item search")
    board_search_azdo.add_argument("project")
    board_search_azdo.add_argument("--query", required=True)
    board_search_azdo.set_defaults(repo=None)
    _add_board_search_filters(board_search_azdo)
    _add_output_format(board_search_azdo)
    _set_handler(board_search_azdo, handle_board_search, "board.search")

    board_search_github = board_search_provider.add_parser("github", help="GitHub issue search")
    board_search_github.add_argument("repo")
    board_search_github.add_argument("--query", required=True)
    board_search_github.set_defaults(project=None)
    _add_board_search_filters(board_search_github)
    _add_output_format(board_search_github)
    _set_handler(board_search_github, handle_board_search, "board.search")

    board_mine = board_sub.add_parser("mine", help="Get my assigned work items/issues")
    board_mine_provider = board_mine.add_subparsers(dest="provider", required=True)

    board_mine_azdo = board_mine_provider.add_parser("azdo", help="Azure DevOps assigned work items")
    board_mine_azdo.add_argument("project")
    board_mine_azdo.add_argument("--include-closed", action="store_true")
    board_mine_azdo.add_argument("--skip", type=int, default=0)
    board_mine_azdo.add_argument("--take", type=int, default=20)
    board_mine_azdo.set_defaults(repo=None)
    _add_output_format(board_mine_azdo)
    _set_handler(board_mine_azdo, handle_board_mine, "board.mine")

    board_mine_github = board_mine_provider.add_parser("github", help="GitHub assigned issues")
    board_mine_github.add_argument("repo")
    board_mine_github.add_argument("--include-closed", action="store_true")
    board_mine_github.add_argument("--skip", type=int, default=0)
    board_mine_github.add_argument("--take", type=int, default=20)
    board_mine_github.set_defaults(project=None)
    _add_output_format(board_mine_github)
    _set_handler(board_mine_github, handle_board_mine, "board.mine")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smith",
        description="Thanos-style Azure DevOps and GitHub read-only workflows with local credentials.",
    )
    parser.add_argument(
        "--org-url",
        dest="org_url",
        default=None,
        help="Override AZURE_DEVOPS_ORG_URL for this invocation.",
    )

    root_subparsers = parser.add_subparsers(dest="group", required=True)

    projects = root_subparsers.add_parser("projects", help="Project discovery commands")
    projects_sub = projects.add_subparsers(dest="action", required=True)
    projects_list = projects_sub.add_parser("list", help="List projects by provider")
    projects_list_provider = projects_list.add_subparsers(dest="provider", required=True)

    projects_list_azdo = projects_list_provider.add_parser("azdo", help="List Azure DevOps projects")
    _add_output_format(projects_list_azdo)
    _set_handler(projects_list_azdo, handle_projects_list, "projects.list")

    projects_list_github = projects_list_provider.add_parser("github", help="List GitHub org project entry")
    _add_output_format(projects_list_github)
    _set_handler(projects_list_github, handle_projects_list, "projects.list")

    repos = root_subparsers.add_parser("repos", help="Repository discovery commands")
    repos_sub = repos.add_subparsers(dest="action", required=True)
    repos_list = repos_sub.add_parser("list", help="List repositories by provider")
    repos_list_provider = repos_list.add_subparsers(dest="provider", required=True)

    repos_list_azdo = repos_list_provider.add_parser("azdo", help="List Azure DevOps project repositories")
    repos_list_azdo.add_argument("project", help="Azure DevOps project name")
    _add_output_format(repos_list_azdo)
    _set_handler(repos_list_azdo, handle_repos_list, "repos.list")

    repos_list_github = repos_list_provider.add_parser("github", help="List GitHub org repositories")
    repos_list_github.set_defaults(project=None)
    _add_output_format(repos_list_github)
    _set_handler(repos_list_github, handle_repos_list, "repos.list")

    code = root_subparsers.add_parser("code", help="Code search and grep commands")
    code_sub = code.add_subparsers(dest="action", required=True)

    code_search = code_sub.add_parser("search", help="Broad code search across configured providers")
    code_search.add_argument("query", nargs="?", help="Search query text")
    code_search.add_argument("--project", help="Project filter")
    code_search.add_argument("--repos", type=_csv_list, help="Comma-separated repository names")
    code_search.add_argument("--skip", type=int, default=0, help="Results offset")
    code_search.add_argument("--take", type=int, default=20, help="Results count")
    _add_search_provider_option(code_search)
    _add_output_format(code_search)
    _set_handler(code_search, handle_code_search, "code.search")

    code_grep = code_sub.add_parser("grep", help="Targeted grep in a provider-specific repository")
    code_grep_provider = code_grep.add_subparsers(dest="provider", required=True)

    code_grep_azdo = code_grep_provider.add_parser("azdo", help="Grep Azure DevOps repository")
    code_grep_azdo.add_argument("project", help="Azure DevOps project name")
    code_grep_azdo.add_argument("repo", help="Repository name")
    _add_grep_options(code_grep_azdo)
    _add_output_format(code_grep_azdo)
    _set_handler(code_grep_azdo, handle_code_grep, "code.grep")

    code_grep_github = code_grep_provider.add_parser("github", help="Grep GitHub repository")
    code_grep_github.add_argument("repo", help="Repository name")
    code_grep_github.set_defaults(project=None)
    _add_grep_options(code_grep_github)
    _add_output_format(code_grep_github)
    _set_handler(code_grep_github, handle_code_grep, "code.grep")

    pr = root_subparsers.add_parser("pr", help="Pull request read commands")
    pr_sub = pr.add_subparsers(dest="action", required=True)

    pr_list = pr_sub.add_parser("list", help="List pull requests")
    pr_list_provider = pr_list.add_subparsers(dest="provider", required=True)

    pr_list_azdo = pr_list_provider.add_parser("azdo", help="List Azure DevOps pull requests")
    pr_list_azdo.add_argument("project", help="Azure DevOps project name")
    pr_list_azdo.add_argument("repo", help="Repository name")
    _add_pr_list_filters(pr_list_azdo)
    _add_output_format(pr_list_azdo)
    _set_handler(pr_list_azdo, handle_pr_list, "pr.list")

    pr_list_github = pr_list_provider.add_parser("github", help="List GitHub pull requests")
    pr_list_github.add_argument("repo", help="Repository name")
    _add_pr_list_filters(pr_list_github)
    _add_output_format(pr_list_github)
    _set_handler(pr_list_github, handle_pr_list, "pr.list")

    pr_get = pr_sub.add_parser("get", help="Get pull request details")
    pr_get_provider = pr_get.add_subparsers(dest="provider", required=True)

    pr_get_azdo = pr_get_provider.add_parser("azdo", help="Get Azure DevOps pull request details")
    pr_get_azdo.add_argument("project")
    pr_get_azdo.add_argument("repo")
    pr_get_azdo.add_argument("id", type=int)
    _add_output_format(pr_get_azdo)
    _set_handler(pr_get_azdo, handle_pr_get, "pr.get")

    pr_get_github = pr_get_provider.add_parser("github", help="Get GitHub pull request details")
    pr_get_github.add_argument("repo")
    pr_get_github.add_argument("id", type=int)
    pr_get_github.set_defaults(project=None)
    _add_output_format(pr_get_github)
    _set_handler(pr_get_github, handle_pr_get, "pr.get")

    pr_threads = pr_sub.add_parser("threads", help="Get pull request comment threads")
    pr_threads_provider = pr_threads.add_subparsers(dest="provider", required=True)

    pr_threads_azdo = pr_threads_provider.add_parser("azdo", help="Get Azure DevOps PR threads")
    pr_threads_azdo.add_argument("project")
    pr_threads_azdo.add_argument("repo")
    pr_threads_azdo.add_argument("id", type=int)
    _add_output_format(pr_threads_azdo)
    _set_handler(pr_threads_azdo, handle_pr_threads, "pr.threads")

    pr_threads_github = pr_threads_provider.add_parser("github", help="Get GitHub PR threads")
    pr_threads_github.add_argument("repo")
    pr_threads_github.add_argument("id", type=int)
    pr_threads_github.set_defaults(project=None)
    _add_output_format(pr_threads_github)
    _set_handler(pr_threads_github, handle_pr_threads, "pr.threads")

    build = root_subparsers.add_parser("build", help="Build log read commands")
    build_sub = build.add_subparsers(dest="action", required=True)

    build_logs = build_sub.add_parser("logs", help="List logs for a build")
    build_logs_provider = build_logs.add_subparsers(dest="provider", required=True)

    build_logs_azdo = build_logs_provider.add_parser("azdo", help="List Azure DevOps build logs")
    build_logs_azdo.add_argument("project")
    build_logs_azdo.add_argument("id", type=int, help="Build ID")
    build_logs_azdo.set_defaults(repo=None)
    _add_output_format(build_logs_azdo)
    _set_handler(build_logs_azdo, handle_build_logs, "build.logs")

    build_logs_github = build_logs_provider.add_parser("github", help="List GitHub Actions run logs")
    build_logs_github.add_argument("repo")
    build_logs_github.add_argument("id", type=int, help="Run ID")
    build_logs_github.set_defaults(project=None)
    _add_output_format(build_logs_github)
    _set_handler(build_logs_github, handle_build_logs, "build.logs")

    build_grep = build_sub.add_parser("grep", help="Search or read build logs")
    build_grep_provider = build_grep.add_subparsers(dest="provider", required=True)

    build_grep_azdo = build_grep_provider.add_parser("azdo", help="Search Azure DevOps build logs")
    build_grep_azdo.add_argument("project")
    build_grep_azdo.add_argument("id", type=int, help="Build ID")
    build_grep_azdo.set_defaults(repo=None)
    _add_build_grep_options(build_grep_azdo)
    _add_output_format(build_grep_azdo)
    _set_handler(build_grep_azdo, handle_build_grep, "build.grep")

    build_grep_github = build_grep_provider.add_parser("github", help="Search GitHub Actions run logs")
    build_grep_github.add_argument("repo")
    build_grep_github.add_argument("id", type=int, help="Run ID")
    build_grep_github.set_defaults(project=None)
    _add_build_grep_options(build_grep_github)
    _add_output_format(build_grep_github)
    _set_handler(build_grep_github, handle_build_grep, "build.grep")

    _add_board_group(root_subparsers, "board")
    _add_board_group(root_subparsers, "stories")

    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]

    deprecated = _deprecated_syntax_message(raw_argv)
    if deprecated:
        print(deprecated, file=sys.stderr)
        return EXIT_INVALID_ARGS

    parser = build_parser()
    try:
        args = parser.parse_args(raw_argv)
    except SystemExit as exc:
        return int(exc.code)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return EXIT_INVALID_ARGS

    command = getattr(args, "command_id", "unknown")

    try:
        _normalize_legacy_text_args(args)
        validate_args_for_provider(args)
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
