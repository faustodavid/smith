#!/usr/bin/env python3
from __future__ import annotations

import argparse
from typing import Any, Callable, Never

from smith.cli.handlers import (
    _csv_list,
    handle_cache_clean,
    handle_ci_grep,
    handle_ci_logs,
    handle_code_grep,
    handle_code_search,
    handle_config_disable,
    handle_config_enable,
    handle_config_init,
    handle_config_list,
    handle_config_path,
    handle_config_show,
    handle_discover_projects,
    handle_discover_repos,
    handle_pr_get,
    handle_pr_list,
    handle_pr_threads,
    handle_work_get,
    handle_work_mine,
    handle_work_search,
)
from smith.config import RemoteConfig, SmithConfig, load_config

_NO_REMOTES_CONFIGURED_HINT = "No remotes configured. Run `smith config init` and add entries under `remotes:`."
_CURRENT_REMOTE_HINT: str | None = None


class SmithArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args: Any, remote_hint: str | None = None, **kwargs: Any) -> None:
        self._remote_hint = _CURRENT_REMOTE_HINT if remote_hint is None else remote_hint
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> Never:
        if self._remote_hint and (
            "argument remote" in message
            or "argument --remote" in message
            or "required: remote" in message
        ):
            message = f"{message}\n{self._remote_hint}"
        super().error(message)


def _resolve_parser_config(*, smith_config: SmithConfig | None = None) -> tuple[list[RemoteConfig], str | None]:
    if smith_config is not None:
        remotes = list(smith_config.remotes.values())
        if remotes:
            return remotes, None
        return [], _NO_REMOTES_CONFIGURED_HINT

    try:
        config = load_config()
    except Exception as exc:
        return [], str(exc)

    remotes = list(config.remotes.values())
    if remotes:
        return remotes, None
    return [], _NO_REMOTES_CONFIGURED_HINT


def _add_output_format(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )


def _add_search_remote_option(
    parser: argparse.ArgumentParser,
    *,
    remotes: list[RemoteConfig],
    remote_hint: str | None,
) -> None:
    remote_names = [remote.name for remote in remotes]
    if remote_names:
        parser.add_argument(
            "--remote",
            choices=["all", *remote_names],
            default="all",
            help="Remote target (default: all)",
        )
        return

    parser.add_argument(
        "--remote",
        default="all",
        help=f"Remote target (default: all). {remote_hint or _NO_REMOTES_CONFIGURED_HINT}",
    )


def _set_handler(
    parser: argparse.ArgumentParser,
    handler: Callable[..., int],
    command_id: str,
    *,
    primary_path: str,
    requires_client: bool = True,
) -> None:
    parser.set_defaults(
        handler=handler,
        command_id=command_id,
        primary_path=primary_path,
        requires_client=requires_client,
    )


def _set_remote_defaults(
    parser: argparse.ArgumentParser,
    *,
    remote: RemoteConfig,
) -> None:
    parser.set_defaults(remote=remote.name, remote_provider=remote.provider)


def _add_parser(
    subparsers: Any,
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
        help='Regex pattern. Use ".*" to match all. Form: smith code grep <remote> <scope> "<regex>"',
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
    parser.add_argument("--no-clone", action="store_true", help="Skip local clone and fetch files via provider APIs instead")


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
        help='Regex pattern. Use ".*" to match all. Form: smith pipelines logs grep <remote> <scope> <id> "<regex>"',
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


def _add_repos_group(
    root_subparsers: Any,
    *,
    remotes: list[RemoteConfig],
) -> None:
    repos = _add_parser(
        root_subparsers,
        "repos",
        help_text="List repositories",
    )
    repos_remote = repos.add_subparsers(dest="remote", required=True)

    for remote in remotes:
        help_text = f"List repositories from {remote.provider} remote '{remote.name}'"
        remote_parser = _add_parser(repos_remote, remote.name, help_text=help_text)
        if remote.provider == "azdo":
            remote_parser.add_argument("project", nargs="?", help="Azure DevOps project name")
        else:
            remote_parser.set_defaults(project=None)
        _set_remote_defaults(remote_parser, remote=remote)
        _add_output_format(remote_parser)
        _set_handler(
            remote_parser,
            handle_discover_repos,
            "repos",
            primary_path="repos",
        )


def _add_orgs_group(
    root_subparsers: Any,
    *,
    remotes: list[RemoteConfig],
) -> None:
    orgs = _add_parser(
        root_subparsers,
        "orgs",
        help_text="List the configured org, group, or project scope for a remote",
    )
    orgs_remote = orgs.add_subparsers(dest="remote", required=True)

    for remote in remotes:
        help_text = f"Show the configured scope for remote '{remote.name}'"
        remote_parser = _add_parser(orgs_remote, remote.name, help_text=help_text)
        _set_remote_defaults(remote_parser, remote=remote)
        _add_output_format(remote_parser)
        _set_handler(
            remote_parser,
            handle_discover_projects,
            "orgs",
            primary_path="orgs",
        )


def _add_code_group(
    root_subparsers: Any,
    *,
    remotes: list[RemoteConfig],
    remote_hint: str | None,
) -> None:
    code = _add_parser(root_subparsers, "code", help_text="Search and grep across remotes and repos")
    code_sub = code.add_subparsers(dest="action", required=True)

    code_search = _add_parser(
        code_sub,
        "search",
        help_text="Broad code search across configured remotes",
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
    code_search.add_argument("--skip", type=int, default=0, help="Results offset")
    code_search.add_argument("--take", type=int, default=20, help="Results count")
    _add_search_remote_option(code_search, remotes=remotes, remote_hint=remote_hint)
    _add_output_format(code_search)
    _set_handler(code_search, handle_code_search, "code.search", primary_path="code search")

    code_grep = _add_parser(
        code_sub,
        "grep",
        help_text="Targeted grep in a configured remote repository",
    )
    code_grep_remote = code_grep.add_subparsers(dest="remote", required=True)

    for remote in remotes:
        help_text = f"Grep a repository via remote '{remote.name}'"
        remote_parser = _add_parser(code_grep_remote, remote.name, help_text=help_text)
        if remote.provider == "azdo":
            remote_parser.add_argument("project", help="Azure DevOps project name")
            remote_parser.add_argument("repo", help="Repository name")
        elif remote.provider == "github":
            remote_parser.add_argument("repo", help="Repository name")
            remote_parser.set_defaults(project=None)
        else:
            remote_parser.add_argument("repo", help="Repository path relative to the configured GitLab group")
            remote_parser.set_defaults(project=None)
        _set_remote_defaults(remote_parser, remote=remote)
        _add_grep_options(remote_parser)
        _add_output_format(remote_parser)
        _set_handler(remote_parser, handle_code_grep, "code.grep", primary_path="code grep")


def _add_config_group(root_subparsers: Any) -> None:
    config = _add_parser(root_subparsers, "config", help_text="Manage remote configurations")
    config_sub = config.add_subparsers(dest="action", required=True)

    config_list = _add_parser(config_sub, "list", help_text="List all configured remotes")
    _add_output_format(config_list)
    _set_handler(
        config_list,
        handle_config_list,
        "config.list",
        primary_path="config list",
        requires_client=False,
    )

    config_show = _add_parser(config_sub, "show", help_text="Show details of a remote")
    config_show.add_argument("remote_name", help="Name of the remote to show")
    _add_output_format(config_show)
    _set_handler(
        config_show,
        handle_config_show,
        "config.show",
        primary_path="config show",
        requires_client=False,
    )

    config_init = _add_parser(config_sub, "init", help_text="Initialize config file")
    _add_output_format(config_init)
    _set_handler(
        config_init,
        handle_config_init,
        "config.init",
        primary_path="config init",
        requires_client=False,
    )

    config_path_cmd = _add_parser(config_sub, "path", help_text="Print config file path")
    _add_output_format(config_path_cmd)
    _set_handler(
        config_path_cmd,
        handle_config_path,
        "config.path",
        primary_path="config path",
        requires_client=False,
    )

    config_enable = _add_parser(config_sub, "enable", help_text="Enable a remote")
    config_enable.add_argument("remote_name", help="Name of the remote to enable")
    _add_output_format(config_enable)
    _set_handler(
        config_enable,
        handle_config_enable,
        "config.enable",
        primary_path="config enable",
        requires_client=False,
    )

    config_disable = _add_parser(config_sub, "disable", help_text="Disable a remote")
    config_disable.add_argument("remote_name", help="Name of the remote to disable")
    _add_output_format(config_disable)
    _set_handler(
        config_disable,
        handle_config_disable,
        "config.disable",
        primary_path="config disable",
        requires_client=False,
    )


def _add_cache_group(root_subparsers: Any, *, remotes: list[RemoteConfig]) -> None:
    cache = _add_parser(root_subparsers, "cache", help_text="Manage local grep caches")
    cache_sub = cache.add_subparsers(dest="action", required=True)

    cache_clean = _add_parser(cache_sub, "clean", help_text="Remove local grep caches")
    remote_choices = ["all", *[remote.name for remote in remotes]]
    cache_clean.add_argument(
        "--remote",
        dest="cache_remote",
        choices=remote_choices,
        default="all",
        help="Remote whose grep cache should be cleaned (default: all)",
    )
    _add_output_format(cache_clean)
    _set_handler(
        cache_clean,
        handle_cache_clean,
        "cache.clean",
        primary_path="cache clean",
        requires_client=False,
    )


def _add_pr_group(
    root_subparsers: Any,
    *,
    remotes: list[RemoteConfig],
) -> None:
    prs = _add_parser(root_subparsers, "prs", help_text="List, get, and read pull request comments")
    pr_sub = prs.add_subparsers(dest="action", required=True)

    pr_list = _add_parser(pr_sub, "list", help_text="List pull requests")
    pr_list_remote = pr_list.add_subparsers(dest="remote", required=True)

    for remote in remotes:
        remote_parser = _add_parser(pr_list_remote, remote.name, help_text=f"List pull requests via remote '{remote.name}'")
        if remote.provider == "azdo":
            remote_parser.add_argument("project", help="Azure DevOps project name")
            remote_parser.add_argument("repo", help="Repository name")
        elif remote.provider == "github":
            remote_parser.add_argument("repo", help="Repository name")
            remote_parser.set_defaults(project=None)
        else:
            remote_parser.add_argument("repo", help="Repository path relative to the configured GitLab group")
            remote_parser.set_defaults(project=None)
        _set_remote_defaults(remote_parser, remote=remote)
        _add_pr_list_filters(remote_parser)
        _add_output_format(remote_parser)
        _set_handler(remote_parser, handle_pr_list, "prs.list", primary_path="prs list")

    pr_get = _add_parser(pr_sub, "get", help_text="Get pull request details")
    pr_get_remote = pr_get.add_subparsers(dest="remote", required=True)

    for remote in remotes:
        remote_parser = _add_parser(pr_get_remote, remote.name, help_text=f"Get pull request details via remote '{remote.name}'")
        if remote.provider == "azdo":
            remote_parser.add_argument("project")
            remote_parser.add_argument("repo")
        else:
            remote_parser.add_argument("repo")
            remote_parser.set_defaults(project=None)
        remote_parser.add_argument("id", type=int)
        _set_remote_defaults(remote_parser, remote=remote)
        _add_output_format(remote_parser)
        _set_handler(remote_parser, handle_pr_get, "prs.get", primary_path="prs get")

    pr_threads = _add_parser(pr_sub, "threads", help_text="Get pull request comment threads")
    pr_threads_remote = pr_threads.add_subparsers(dest="remote", required=True)

    for remote in remotes:
        remote_parser = _add_parser(pr_threads_remote, remote.name, help_text=f"Get pull request threads via remote '{remote.name}'")
        if remote.provider == "azdo":
            remote_parser.add_argument("project")
            remote_parser.add_argument("repo")
        else:
            remote_parser.add_argument("repo")
            remote_parser.set_defaults(project=None)
        remote_parser.add_argument("id", type=int)
        _set_remote_defaults(remote_parser, remote=remote)
        _add_output_format(remote_parser)
        _set_handler(remote_parser, handle_pr_threads, "prs.threads", primary_path="prs threads")


def _add_ci_group(
    root_subparsers: Any,
    *,
    remotes: list[RemoteConfig],
) -> None:
    pipelines = _add_parser(root_subparsers, "pipelines", help_text="Read and grep pipeline logs")
    ci_sub = pipelines.add_subparsers(dest="action", required=True)

    ci_logs = _add_parser(ci_sub, "logs", help_text="Inspect pipeline logs")
    ci_logs_sub = ci_logs.add_subparsers(dest="log_action", required=True)

    ci_logs_list = _add_parser(ci_logs_sub, "list", help_text="List logs for a pipeline run")
    ci_logs_list_remote = ci_logs_list.add_subparsers(dest="remote", required=True)

    for remote in remotes:
        remote_parser = _add_parser(ci_logs_list_remote, remote.name, help_text=f"List pipeline logs via remote '{remote.name}'")
        if remote.provider == "azdo":
            remote_parser.add_argument("project")
            remote_parser.add_argument("id", type=int, help="Build ID")
            remote_parser.set_defaults(repo=None)
        elif remote.provider == "github":
            remote_parser.add_argument("repo")
            remote_parser.add_argument("id", type=int, help="Run ID")
            remote_parser.set_defaults(project=None)
        else:
            remote_parser.add_argument("repo")
            remote_parser.add_argument("id", type=int, help="Pipeline ID")
            remote_parser.set_defaults(project=None)
        _set_remote_defaults(remote_parser, remote=remote)
        _add_output_format(remote_parser)
        _set_handler(
            remote_parser,
            handle_ci_logs,
            "pipelines.logs.list",
            primary_path="pipelines logs list",
        )

    ci_logs_grep = _add_parser(ci_logs_sub, "grep", help_text="Search or read pipeline logs")
    ci_logs_grep_remote = ci_logs_grep.add_subparsers(dest="remote", required=True)

    for remote in remotes:
        remote_parser = _add_parser(ci_logs_grep_remote, remote.name, help_text=f"Search pipeline logs via remote '{remote.name}'")
        if remote.provider == "azdo":
            remote_parser.add_argument("project")
            remote_parser.add_argument("id", type=int, help="Build ID")
            remote_parser.set_defaults(repo=None)
        elif remote.provider == "github":
            remote_parser.add_argument("repo")
            remote_parser.add_argument("id", type=int, help="Run ID")
            remote_parser.set_defaults(project=None)
        else:
            remote_parser.add_argument("repo")
            remote_parser.add_argument("id", type=int, help="Pipeline ID")
            remote_parser.set_defaults(project=None)
        _set_remote_defaults(remote_parser, remote=remote)
        _add_ci_grep_options(remote_parser)
        _add_output_format(remote_parser)
        _set_handler(
            remote_parser,
            handle_ci_grep,
            "pipelines.logs.grep",
            primary_path="pipelines logs grep",
        )


def _add_stories_group(
    root_subparsers: Any,
    *,
    remotes: list[RemoteConfig],
) -> None:
    stories = _add_parser(
        root_subparsers,
        "stories",
        help_text="Get, search, and get mine",
    )
    stories_sub = stories.add_subparsers(dest="action", required=True)

    stories_get = _add_parser(stories_sub, "get", help_text="Get work item or issue by ID")
    stories_get_remote = stories_get.add_subparsers(dest="remote", required=True)

    for remote in remotes:
        remote_parser = _add_parser(stories_get_remote, remote.name, help_text=f"Get a work item or issue via remote '{remote.name}'")
        if remote.provider == "azdo":
            remote_parser.add_argument("project")
            remote_parser.add_argument("id", type=int)
            remote_parser.set_defaults(repo=None)
        else:
            remote_parser.add_argument("repo")
            remote_parser.add_argument("id", type=int)
            remote_parser.set_defaults(project=None)
        _set_remote_defaults(remote_parser, remote=remote)
        _add_output_format(remote_parser)
        _set_handler(
            remote_parser,
            handle_work_get,
            "stories.get",
            primary_path="stories get",
        )

    stories_search = _add_parser(stories_sub, "search", help_text="Search work items and issues")
    stories_search_remote = stories_search.add_subparsers(dest="remote", required=True)

    for remote in remotes:
        remote_parser = _add_parser(stories_search_remote, remote.name, help_text=f"Search work items or issues via remote '{remote.name}'")
        if remote.provider == "azdo":
            remote_parser.add_argument("project")
            remote_parser.add_argument("--query", required=True)
            remote_parser.set_defaults(repo=None)
        else:
            remote_parser.add_argument("repo")
            remote_parser.add_argument("--query", required=True)
            remote_parser.set_defaults(project=None)
        _set_remote_defaults(remote_parser, remote=remote)
        _add_work_search_filters(remote_parser)
        _add_output_format(remote_parser)
        _set_handler(
            remote_parser,
            handle_work_search,
            "stories.search",
            primary_path="stories search",
        )

    stories_mine = _add_parser(stories_sub, "mine", help_text="Get my assigned work items and issues")
    stories_mine_remote = stories_mine.add_subparsers(dest="remote", required=True)

    for remote in remotes:
        remote_parser = _add_parser(
            stories_mine_remote,
            remote.name,
            help_text=f"Get assigned work items or issues via remote '{remote.name}'",
        )
        if remote.provider == "azdo":
            remote_parser.add_argument("project")
            remote_parser.set_defaults(repo=None)
        else:
            remote_parser.add_argument("repo")
            remote_parser.set_defaults(project=None)
        remote_parser.add_argument("--include-closed", action="store_true")
        remote_parser.add_argument("--skip", type=int, default=0)
        remote_parser.add_argument("--take", type=int, default=20)
        _set_remote_defaults(remote_parser, remote=remote)
        _add_output_format(remote_parser)
        _set_handler(
            remote_parser,
            handle_work_mine,
            "stories.mine",
            primary_path="stories mine",
        )


def build_parser(*, smith_config: SmithConfig | None = None) -> argparse.ArgumentParser:
    global _CURRENT_REMOTE_HINT
    remotes, remote_hint = _resolve_parser_config(smith_config=smith_config)
    _CURRENT_REMOTE_HINT = remote_hint
    parser = SmithArgumentParser(
        prog="smith",
        description="Smith Azure DevOps, GitHub, and GitLab read-only workflows with local credentials.",
        remote_hint=remote_hint,
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
        metavar="{repos,orgs,code,config,cache,prs,pipelines,stories}",
    )

    _add_repos_group(root_subparsers, remotes=remotes)
    _add_orgs_group(root_subparsers, remotes=remotes)
    _add_code_group(root_subparsers, remotes=remotes, remote_hint=remote_hint)
    _add_config_group(root_subparsers)
    _add_cache_group(root_subparsers, remotes=remotes)
    _add_pr_group(root_subparsers, remotes=remotes)
    _add_ci_group(root_subparsers, remotes=remotes)
    _add_stories_group(root_subparsers, remotes=remotes)

    return parser
