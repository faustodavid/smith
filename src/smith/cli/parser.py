#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import Any, Callable

from smith.cli.handlers import (
    _csv_list,
    handle_ci_grep,
    handle_ci_logs,
    handle_code_grep,
    handle_code_search,
    handle_discover_projects,
    handle_discover_repos,
    handle_pr_get,
    handle_pr_list,
    handle_pr_threads,
    handle_work_get,
    handle_work_mine,
    handle_work_search,
)
from smith.client import SmithClient


class _DeprecatedCsvAppendAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        if values is None:
            raw_values = ""
        elif isinstance(values, str):
            raw_values = values
        else:
            raw_values = ",".join(str(item) for item in values)

        items = list(getattr(namespace, self.dest, None) or [])
        items.extend(_csv_list(raw_values))
        setattr(namespace, self.dest, items)

        deprecated_flags = list(getattr(namespace, "deprecated_flags", None) or [])
        if option_string and option_string not in deprecated_flags:
            deprecated_flags.append(option_string)
        setattr(namespace, "deprecated_flags", deprecated_flags)


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
    handler: Callable[[SmithClient, argparse.Namespace], int],
    command_id: str,
    *,
    primary_path: str,
    alias_used: str | None = None,
) -> None:
    parser.set_defaults(
        handler=handler,
        command_id=command_id,
        primary_path=primary_path,
        alias_used=alias_used,
    )


def _add_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    *,
    help_text: str,
    description: str | None = None,
) -> argparse.ArgumentParser:
    return subparsers.add_parser(
        name,
        help=help_text,
        description=description or help_text,
    )


def _add_grep_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "pattern",
        help='Regex pattern. Use ".*" to match all. Form: smith code grep <provider> <scope> "<regex>"',
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


def _add_ci_grep_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--log-id", type=int)
    parser.add_argument(
        "pattern",
        help='Regex pattern. Use ".*" to match all. Form: smith pipelines logs grep <provider> <scope> <id> "<regex>"',
    )
    parser.add_argument(
        "--output-mode",
        choices=["content", "logs_with_matches", "count"],
        default="content",
    )
    parser.add_argument("--context-lines", type=int, default=3)
    parser.add_argument("--from-line", type=int)
    parser.add_argument("--to-line", type=int)
    parser.add_argument("--case-sensitive", action="store_true")


def _add_work_search_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--area")
    parser.add_argument("--type")
    parser.add_argument("--state")
    parser.add_argument("--assigned-to")
    parser.add_argument("--skip", type=int, default=0)
    parser.add_argument("--take", type=int, default=20)


def _add_repos_group(root_subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    repos = _add_parser(
        root_subparsers,
        "repos",
        help_text="List repositories",
    )
    repos_provider = repos.add_subparsers(dest="provider", required=True)

    repos_azdo = _add_parser(
        repos_provider,
        "azdo",
        help_text="List Azure DevOps repositories",
    )
    repos_azdo.add_argument("project", nargs="?", help="Azure DevOps project name")
    _add_output_format(repos_azdo)
    _set_handler(
        repos_azdo,
        handle_discover_repos,
        "repos",
        primary_path="repos",
    )

    repos_github = _add_parser(
        repos_provider,
        "github",
        help_text="List GitHub repositories",
    )
    repos_github.set_defaults(project=None)
    _add_output_format(repos_github)
    _set_handler(
        repos_github,
        handle_discover_repos,
        "repos",
        primary_path="repos",
    )


def _add_orgs_group(root_subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    orgs = _add_parser(
        root_subparsers,
        "orgs",
        help_text="List GitHub organization or Azure DevOps projects",
    )
    orgs_provider = orgs.add_subparsers(dest="provider", required=True)

    orgs_azdo = _add_parser(
        orgs_provider,
        "azdo",
        help_text="List Azure DevOps projects",
    )
    _add_output_format(orgs_azdo)
    _set_handler(
        orgs_azdo,
        handle_discover_projects,
        "orgs",
        primary_path="orgs",
    )

    orgs_github = _add_parser(
        orgs_provider,
        "github",
        help_text="Show the configured GitHub organization",
    )
    _add_output_format(orgs_github)
    _set_handler(
        orgs_github,
        handle_discover_projects,
        "orgs",
        primary_path="orgs",
    )


def _add_code_group(root_subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    code = _add_parser(root_subparsers, "code", help_text="Search and grep across providers and repos")
    code_sub = code.add_subparsers(dest="action", required=True)

    code_search = _add_parser(
        code_sub,
        "search",
        help_text="Broad code search across configured providers",
    )
    code_search.add_argument("query", nargs="?", help="Search query text")
    code_search.add_argument("--project", help="Azure DevOps project filter")
    code_search.add_argument(
        "--repo",
        dest="repos",
        action="append",
        default=None,
        metavar="REPO",
        help="Repository filter (repeatable)",
    )
    code_search.add_argument("--repos", dest="repos", action=_DeprecatedCsvAppendAction, help=argparse.SUPPRESS)
    code_search.add_argument("--skip", type=int, default=0, help="Results offset")
    code_search.add_argument("--take", type=int, default=20, help="Results count")
    _add_search_provider_option(code_search)
    _add_output_format(code_search)
    _set_handler(code_search, handle_code_search, "code.search", primary_path="code search")

    code_grep = _add_parser(
        code_sub,
        "grep",
        help_text="Targeted grep in a provider-specific repository",
    )
    code_grep_provider = code_grep.add_subparsers(dest="provider", required=True)

    code_grep_azdo = _add_parser(code_grep_provider, "azdo", help_text="Grep Azure DevOps repository")
    code_grep_azdo.add_argument("project", help="Azure DevOps project name")
    code_grep_azdo.add_argument("repo", help="Repository name")
    _add_grep_options(code_grep_azdo)
    _add_output_format(code_grep_azdo)
    _set_handler(code_grep_azdo, handle_code_grep, "code.grep", primary_path="code grep")

    code_grep_github = _add_parser(code_grep_provider, "github", help_text="Grep GitHub repository")
    code_grep_github.add_argument("repo", help="Repository name")
    code_grep_github.set_defaults(project=None)
    _add_grep_options(code_grep_github)
    _add_output_format(code_grep_github)
    _set_handler(code_grep_github, handle_code_grep, "code.grep", primary_path="code grep")


def _add_pr_group(root_subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    prs = _add_parser(root_subparsers, "prs", help_text="List, get, and read pull request comments")
    pr_sub = prs.add_subparsers(dest="action", required=True)

    pr_list = _add_parser(pr_sub, "list", help_text="List pull requests")
    pr_list_provider = pr_list.add_subparsers(dest="provider", required=True)

    pr_list_azdo = _add_parser(pr_list_provider, "azdo", help_text="List Azure DevOps pull requests")
    pr_list_azdo.add_argument("project", help="Azure DevOps project name")
    pr_list_azdo.add_argument("repo", help="Repository name")
    _add_pr_list_filters(pr_list_azdo)
    _add_output_format(pr_list_azdo)
    _set_handler(pr_list_azdo, handle_pr_list, "prs.list", primary_path="prs list")

    pr_list_github = _add_parser(pr_list_provider, "github", help_text="List GitHub pull requests")
    pr_list_github.add_argument("repo", help="Repository name")
    pr_list_github.set_defaults(project=None)
    _add_pr_list_filters(pr_list_github)
    _add_output_format(pr_list_github)
    _set_handler(pr_list_github, handle_pr_list, "prs.list", primary_path="prs list")

    pr_get = _add_parser(pr_sub, "get", help_text="Get pull request details")
    pr_get_provider = pr_get.add_subparsers(dest="provider", required=True)

    pr_get_azdo = _add_parser(pr_get_provider, "azdo", help_text="Get Azure DevOps pull request details")
    pr_get_azdo.add_argument("project")
    pr_get_azdo.add_argument("repo")
    pr_get_azdo.add_argument("id", type=int)
    _add_output_format(pr_get_azdo)
    _set_handler(pr_get_azdo, handle_pr_get, "prs.get", primary_path="prs get")

    pr_get_github = _add_parser(pr_get_provider, "github", help_text="Get GitHub pull request details")
    pr_get_github.add_argument("repo")
    pr_get_github.add_argument("id", type=int)
    pr_get_github.set_defaults(project=None)
    _add_output_format(pr_get_github)
    _set_handler(pr_get_github, handle_pr_get, "prs.get", primary_path="prs get")

    pr_threads = _add_parser(pr_sub, "threads", help_text="Get pull request comment threads")
    pr_threads_provider = pr_threads.add_subparsers(dest="provider", required=True)

    pr_threads_azdo = _add_parser(pr_threads_provider, "azdo", help_text="Get Azure DevOps pull request threads")
    pr_threads_azdo.add_argument("project")
    pr_threads_azdo.add_argument("repo")
    pr_threads_azdo.add_argument("id", type=int)
    _add_output_format(pr_threads_azdo)
    _set_handler(pr_threads_azdo, handle_pr_threads, "prs.threads", primary_path="prs threads")

    pr_threads_github = _add_parser(pr_threads_provider, "github", help_text="Get GitHub pull request threads")
    pr_threads_github.add_argument("repo")
    pr_threads_github.add_argument("id", type=int)
    pr_threads_github.set_defaults(project=None)
    _add_output_format(pr_threads_github)
    _set_handler(pr_threads_github, handle_pr_threads, "prs.threads", primary_path="prs threads")


def _add_ci_group(root_subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    pipelines = _add_parser(root_subparsers, "pipelines", help_text="Read and grep pipeline logs")
    ci_sub = pipelines.add_subparsers(dest="action", required=True)

    ci_logs = _add_parser(ci_sub, "logs", help_text="Inspect pipeline logs")
    ci_logs_sub = ci_logs.add_subparsers(dest="log_action", required=True)

    ci_logs_list = _add_parser(ci_logs_sub, "list", help_text="List logs for a pipeline run")
    ci_logs_list_provider = ci_logs_list.add_subparsers(dest="provider", required=True)

    ci_logs_list_azdo = _add_parser(
        ci_logs_list_provider,
        "azdo",
        help_text="List Azure DevOps build logs",
    )
    ci_logs_list_azdo.add_argument("project")
    ci_logs_list_azdo.add_argument("id", type=int, help="Build ID")
    ci_logs_list_azdo.set_defaults(repo=None)
    _add_output_format(ci_logs_list_azdo)
    _set_handler(
        ci_logs_list_azdo,
        handle_ci_logs,
        "pipelines.logs.list",
        primary_path="pipelines logs list",
    )

    ci_logs_list_github = _add_parser(
        ci_logs_list_provider,
        "github",
        help_text="List GitHub Actions run logs",
    )
    ci_logs_list_github.add_argument("repo")
    ci_logs_list_github.add_argument("id", type=int, help="Run ID")
    ci_logs_list_github.set_defaults(project=None)
    _add_output_format(ci_logs_list_github)
    _set_handler(
        ci_logs_list_github,
        handle_ci_logs,
        "pipelines.logs.list",
        primary_path="pipelines logs list",
    )

    ci_logs_grep = _add_parser(ci_logs_sub, "grep", help_text="Search or read pipeline logs")
    ci_logs_grep_provider = ci_logs_grep.add_subparsers(dest="provider", required=True)

    ci_logs_grep_azdo = _add_parser(
        ci_logs_grep_provider,
        "azdo",
        help_text="Search Azure DevOps build logs",
    )
    ci_logs_grep_azdo.add_argument("project")
    ci_logs_grep_azdo.add_argument("id", type=int, help="Build ID")
    ci_logs_grep_azdo.set_defaults(repo=None)
    _add_ci_grep_options(ci_logs_grep_azdo)
    _add_output_format(ci_logs_grep_azdo)
    _set_handler(
        ci_logs_grep_azdo,
        handle_ci_grep,
        "pipelines.logs.grep",
        primary_path="pipelines logs grep",
    )

    ci_logs_grep_github = _add_parser(
        ci_logs_grep_provider,
        "github",
        help_text="Search GitHub Actions run logs",
    )
    ci_logs_grep_github.add_argument("repo")
    ci_logs_grep_github.add_argument("id", type=int, help="Run ID")
    ci_logs_grep_github.set_defaults(project=None)
    _add_ci_grep_options(ci_logs_grep_github)
    _add_output_format(ci_logs_grep_github)
    _set_handler(
        ci_logs_grep_github,
        handle_ci_grep,
        "pipelines.logs.grep",
        primary_path="pipelines logs grep",
    )


def _add_stories_group(root_subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    stories = _add_parser(
        root_subparsers,
        "stories",
        help_text="Get, search, and get mine",
    )
    stories_sub = stories.add_subparsers(dest="action", required=True)

    stories_get = _add_parser(stories_sub, "get", help_text="Get work item or issue by ID")
    stories_get_provider = stories_get.add_subparsers(dest="provider", required=True)

    stories_get_azdo = _add_parser(stories_get_provider, "azdo", help_text="Azure DevOps work item")
    stories_get_azdo.add_argument("project")
    stories_get_azdo.add_argument("id", type=int)
    stories_get_azdo.set_defaults(repo=None)
    _add_output_format(stories_get_azdo)
    _set_handler(
        stories_get_azdo,
        handle_work_get,
        "stories.get",
        primary_path="stories get",
    )

    stories_get_github = _add_parser(stories_get_provider, "github", help_text="GitHub issue")
    stories_get_github.add_argument("repo")
    stories_get_github.add_argument("id", type=int)
    stories_get_github.set_defaults(project=None)
    _add_output_format(stories_get_github)
    _set_handler(
        stories_get_github,
        handle_work_get,
        "stories.get",
        primary_path="stories get",
    )

    stories_search = _add_parser(stories_sub, "search", help_text="Search work items and issues")
    stories_search_provider = stories_search.add_subparsers(dest="provider", required=True)

    stories_search_azdo = _add_parser(stories_search_provider, "azdo", help_text="Azure DevOps work item search")
    stories_search_azdo.add_argument("project")
    stories_search_azdo.add_argument("--query", required=True)
    stories_search_azdo.set_defaults(repo=None)
    _add_work_search_filters(stories_search_azdo)
    _add_output_format(stories_search_azdo)
    _set_handler(
        stories_search_azdo,
        handle_work_search,
        "stories.search",
        primary_path="stories search",
    )

    stories_search_github = _add_parser(stories_search_provider, "github", help_text="GitHub issue search")
    stories_search_github.add_argument("repo")
    stories_search_github.add_argument("--query", required=True)
    stories_search_github.set_defaults(project=None)
    _add_work_search_filters(stories_search_github)
    _add_output_format(stories_search_github)
    _set_handler(
        stories_search_github,
        handle_work_search,
        "stories.search",
        primary_path="stories search",
    )

    stories_mine = _add_parser(stories_sub, "mine", help_text="Get my assigned work items and issues")
    stories_mine_provider = stories_mine.add_subparsers(dest="provider", required=True)

    stories_mine_azdo = _add_parser(stories_mine_provider, "azdo", help_text="Azure DevOps assigned work items")
    stories_mine_azdo.add_argument("project")
    stories_mine_azdo.add_argument("--include-closed", action="store_true")
    stories_mine_azdo.add_argument("--skip", type=int, default=0)
    stories_mine_azdo.add_argument("--take", type=int, default=20)
    stories_mine_azdo.set_defaults(repo=None)
    _add_output_format(stories_mine_azdo)
    _set_handler(
        stories_mine_azdo,
        handle_work_mine,
        "stories.mine",
        primary_path="stories mine",
    )

    stories_mine_github = _add_parser(stories_mine_provider, "github", help_text="GitHub assigned issues")
    stories_mine_github.add_argument("repo")
    stories_mine_github.add_argument("--include-closed", action="store_true")
    stories_mine_github.add_argument("--skip", type=int, default=0)
    stories_mine_github.add_argument("--take", type=int, default=20)
    stories_mine_github.set_defaults(project=None)
    _add_output_format(stories_mine_github)
    _set_handler(
        stories_mine_github,
        handle_work_mine,
        "stories.mine",
        primary_path="stories mine",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smith",
        description="Smith Azure DevOps and GitHub read-only workflows with local credentials.",
    )
    parser.add_argument(
        "--azdo-org",
        dest="azdo_org",
        default=None,
        help="Override AZURE_DEVOPS_ORG for this invocation.",
    )
    parser.add_argument(
        "--github-org",
        dest="github_org",
        default=None,
        help="Override GITHUB_ORG for this invocation.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose (DEBUG) logging to stderr.",
    )

    root_subparsers = parser.add_subparsers(
        dest="group",
        required=True,
        metavar="{repos,orgs,code,prs,pipelines,stories}",
    )

    _add_repos_group(root_subparsers)
    _add_orgs_group(root_subparsers)
    _add_code_group(root_subparsers)
    _add_pr_group(root_subparsers)
    _add_ci_group(root_subparsers)
    _add_stories_group(root_subparsers)

    return parser
