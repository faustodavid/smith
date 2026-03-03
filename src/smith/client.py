#!/usr/bin/env python3
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Callable, Literal

import requests

from smith.config import parse_runtime_config
from smith.errors import SmithApiError, SmithAuthError, SmithError
from smith.fanout import run_fanout
from smith.http import configure_http_session
from smith.providers.azdo import AzdoProvider
from smith.providers.base import normalize_provider, normalize_single_provider, resolve_providers
from smith.providers.github import GITHUB_DEFAULT_API_URL, GITHUB_DEFAULT_API_VERSION, GitHubProvider


class SmithClient:
    def __init__(
        self,
        *,
        azdo_org: str | None = None,
        github_org: str | None = None,
        api_version: str | None = None,
        timeout_seconds: int | None = None,
        max_output_chars: int | None = None,
        credential: Any | None = None,
        session: requests.Session | None = None,
    ) -> None:
        runtime = parse_runtime_config(
            azdo_org=azdo_org,
            api_version=api_version,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
            github_org=github_org,
            github_api_url_default=GITHUB_DEFAULT_API_URL,
            github_api_version_default=GITHUB_DEFAULT_API_VERSION,
        )

        if not runtime.azdo_configured and not runtime.github_configured:
            raise ValueError(
                "No providers configured. Set at least one of:\n"
                "  - AZURE_DEVOPS_ORG (for Azure DevOps)\n"
                "  - GITHUB_ORG (for GitHub)"
            )

        main_session = session or requests.Session()
        configure_http_session(
            main_session,
            pool_connections=runtime.http_pool_connections,
            pool_maxsize=runtime.http_pool_maxsize,
        )

        self._runtime = runtime
        self._credential = credential
        self._main_session = main_session
        self._azdo: AzdoProvider | None = None
        self._github: GitHubProvider | None = None

        self.azdo_org = runtime.azdo_org
        self.api_version = runtime.api_version
        self.timeout_seconds = runtime.timeout_seconds
        self.max_output_chars = runtime.max_output_chars
        self.github_org = runtime.github_org
        self.github_api_url = runtime.github_api_url
        self.github_api_version = runtime.github_api_version
        self.github_timeout_seconds = runtime.github_timeout_seconds

    def _get_azdo(self) -> AzdoProvider:
        if self._azdo is None:
            if not self._runtime.azdo_configured:
                raise ValueError(
                    "Azure DevOps is not configured. "
                    "Set AZURE_DEVOPS_ORG to enable this provider."
                )
            self._azdo = AzdoProvider(
                config=self._runtime,
                credential=self._credential,
                session=self._main_session,
            )
        return self._azdo

    def _get_github(self) -> GitHubProvider:
        if self._github is None:
            if not self._runtime.github_configured:
                raise ValueError(
                    "GitHub is not configured. "
                    "Set GITHUB_ORG to enable this provider."
                )
            self._github = GitHubProvider(
                config=self._runtime,
                session=self._main_session,
            )
        return self._github

    @staticmethod
    def _provider_warnings_and_partial(payload: Any) -> tuple[list[str], bool]:
        if not isinstance(payload, dict):
            return [], False
        warnings = payload.get("warnings")
        if isinstance(warnings, list):
            warning_list = [str(item) for item in warnings if str(item).strip()]
        else:
            warning_list = []
        partial = bool(payload.get("partial", False))
        return warning_list, partial

    @staticmethod
    def _provider_entry_success(payload: Any) -> dict[str, Any]:
        warnings, partial = SmithClient._provider_warnings_and_partial(payload)
        return {
            "ok": True,
            "data": payload,
            "warnings": warnings,
            "partial": partial,
            "error": None,
        }

    @staticmethod
    def _provider_entry_error(code: str, message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "data": None,
            "warnings": [],
            "partial": False,
            "error": {"code": code, "message": message},
        }

    def _fanout(
        self,
        *,
        provider: str,
        operations: dict[str, Callable[[], Any]],
    ) -> dict[str, Any]:
        requested_provider = normalize_provider(provider)
        providers = resolve_providers(requested_provider)
        return run_fanout(
            providers=providers,
            requested_provider=requested_provider,
            operations=operations,
            provider_entry_success=self._provider_entry_success,
            provider_entry_error=self._provider_entry_error,
        )

    def execute_projects_list(self, *, provider: str) -> dict[str, Any]:
        single_provider = normalize_single_provider(provider, command="projects.list")
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self._get_azdo().list_projects(),
                "github": lambda: self._get_github().list_projects(),
            },
        )

    def execute_repos_list(
        self,
        *,
        provider: str,
        project: str | None,
    ) -> dict[str, Any]:
        single_provider = normalize_single_provider(provider, command="repos.list")
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self._get_azdo().list_repositories(project=str(project)),
                "github": lambda: self._get_github().list_repositories(),
            },
        )

    def execute_code_search(
        self,
        *,
        provider: str,
        query: str,
        project: str | None,
        repos: list[str] | None,
        skip: int,
        take: int,
    ) -> dict[str, Any]:
        return self._fanout(
            provider=provider,
            operations={
                "azdo": lambda: self._get_azdo().search_code(
                    query=query,
                    project=project,
                    repos=repos,
                    skip=skip,
                    take=take,
                ),
                "github": lambda: self._get_github().search_code(
                    query=query,
                    project=project,
                    repos=repos,
                    skip=skip,
                    take=take,
                ),
            },
        )

    def execute_code_grep(
        self,
        *,
        provider: str,
        project: str | None,
        repo: str,
        pattern: str | None,
        path: str | None,
        branch: str | None,
        glob: str | None,
        output_mode: Literal["content", "files_with_matches", "count"],
        case_insensitive: bool,
        context_lines: int | None,
        from_line: int | None,
        to_line: int | None,
    ) -> dict[str, Any]:
        single_provider = normalize_single_provider(provider, command="code.grep")
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self._get_azdo().grep(
                    project=str(project),
                    repo=repo,
                    pattern=pattern,
                    path=path,
                    branch=branch,
                    glob=glob,
                    output_mode=output_mode,
                    case_insensitive=case_insensitive,
                    context_lines=context_lines,
                    from_line=from_line,
                    to_line=to_line,
                ),
                "github": lambda: self._get_github().grep(
                    repo=repo,
                    pattern=pattern,
                    path=path,
                    branch=branch,
                    glob=glob,
                    output_mode=output_mode,
                    case_insensitive=case_insensitive,
                    context_lines=context_lines,
                    from_line=from_line,
                    to_line=to_line,
                ),
            },
        )

    def execute_pr_list(
        self,
        *,
        provider: str,
        projects: list[str] | None,
        repos: list[str] | None,
        statuses: list[str] | None,
        creators: list[str] | None,
        date_from: str | datetime | None,
        date_to: str | datetime | None,
        skip: int,
        take: int,
        exclude_drafts: bool,
        include_labels: bool,
    ) -> dict[str, Any]:
        single_provider = normalize_single_provider(provider, command="pr.list")
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self._get_azdo().list_pull_requests(
                    projects=projects,
                    repos=repos,
                    statuses=statuses,
                    creators=creators,
                    date_from=date_from,
                    date_to=date_to,
                    skip=skip,
                    take=take,
                    exclude_drafts=exclude_drafts,
                    include_labels=include_labels,
                ),
                "github": lambda: self._get_github().list_pull_requests(
                    repos=repos or projects,
                    statuses=statuses,
                    creators=creators,
                    date_from=date_from,
                    date_to=date_to,
                    skip=skip,
                    take=take,
                    exclude_drafts=exclude_drafts,
                    include_labels=include_labels,
                ),
            },
        )

    def execute_pr_get(
        self,
        *,
        provider: str,
        project: str | None,
        repo: str,
        pull_request_id: int,
    ) -> dict[str, Any]:
        single_provider = normalize_single_provider(provider, command="pr.get")
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self._get_azdo().get_pull_request(
                    project=str(project),
                    repo=repo,
                    pull_request_id=pull_request_id,
                ),
                "github": lambda: self._get_github().get_pull_request(
                    repo=repo,
                    pull_request_id=pull_request_id,
                ),
            },
        )

    def execute_pr_threads(
        self,
        *,
        provider: str,
        project: str | None,
        repo: str,
        pull_request_id: int,
    ) -> dict[str, Any]:
        single_provider = normalize_single_provider(provider, command="pr.threads")
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self._get_azdo().get_pull_request_threads(
                    project=str(project),
                    repo=repo,
                    pull_request_id=pull_request_id,
                ),
                "github": lambda: self._get_github().get_pull_request_threads(
                    repo=repo,
                    pull_request_id=pull_request_id,
                ),
            },
        )

    def execute_build_logs(
        self,
        *,
        provider: str,
        project: str | None,
        repo: str | None,
        build_id: int,
    ) -> dict[str, Any]:
        single_provider = normalize_single_provider(provider, command="build.logs")
        effective_repo = repo or project
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self._get_azdo().get_build_log(project=str(project), build_id=build_id),
                "github": lambda: self._get_github().get_build_log(repo=str(effective_repo), build_id=build_id),
            },
        )

    def execute_build_grep(
        self,
        *,
        provider: str,
        project: str | None,
        repo: str | None,
        build_id: int,
        log_id: int | None,
        pattern: str | None,
        output_mode: Literal["content", "logs_with_matches", "count"],
        case_insensitive: bool,
        context_lines: int | None,
        from_line: int | None,
        to_line: int | None,
    ) -> dict[str, Any]:
        single_provider = normalize_single_provider(provider, command="build.grep")
        effective_repo = repo or project
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self._get_azdo().grep_build_log(
                    project=str(project),
                    build_id=build_id,
                    log_id=log_id,
                    pattern=pattern,
                    output_mode=output_mode,
                    case_insensitive=case_insensitive,
                    context_lines=context_lines,
                    from_line=from_line,
                    to_line=to_line,
                ),
                "github": lambda: self._get_github().grep_build_log(
                    repo=str(effective_repo),
                    build_id=build_id,
                    log_id=log_id,
                    pattern=pattern,
                    output_mode=output_mode,
                    case_insensitive=case_insensitive,
                    context_lines=context_lines,
                    from_line=from_line,
                    to_line=to_line,
                ),
            },
        )

    def execute_board_ticket(
        self,
        *,
        provider: str,
        project: str | None,
        repo: str | None,
        work_item_id: int,
    ) -> dict[str, Any]:
        single_provider = normalize_single_provider(provider, command="board.ticket")
        effective_repo = repo or project
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self._get_azdo().get_ticket_by_id(project=str(project), work_item_id=work_item_id),
                "github": lambda: self._get_github().get_ticket_by_id(repo=str(effective_repo), work_item_id=work_item_id),
            },
        )

    def execute_board_list(
        self,
        *,
        provider: str,
        project: str | None,
        wiql: str,
        skip: int,
        take: int,
    ) -> dict[str, Any]:
        single_provider = normalize_single_provider(provider, command="board.list")
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self._get_azdo().list_work_items(
                    project=str(project),
                    wiql=wiql,
                    skip=skip,
                    take=take,
                ),
                "github": lambda: (_ for _ in ()).throw(
                    ValueError("GitHub does not support `board list`. Use `board search` instead.")
                ),
            },
        )

    def execute_board_search(
        self,
        *,
        provider: str,
        query: str,
        project: str | None,
        repo: str | None,
        area: str | None,
        work_item_type: str | None,
        state: str | None,
        assigned_to: str | None,
        skip: int,
        take: int,
    ) -> dict[str, Any]:
        single_provider = normalize_single_provider(provider, command="board.search")
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self._get_azdo().search_work_items(
                    query=query,
                    project=project,
                    area=area,
                    work_item_type=work_item_type,
                    state=state,
                    assigned_to=assigned_to,
                    skip=skip,
                    take=take,
                ),
                "github": lambda: self._get_github().search_work_items(
                    query=query,
                    project=project,
                    repo=repo,
                    state=state,
                    assigned_to=assigned_to,
                    skip=skip,
                    take=take,
                    include_closed=True,
                ),
            },
        )

    def execute_board_mine(
        self,
        *,
        provider: str,
        project: str | None,
        repo: str | None,
        include_closed: bool,
        skip: int,
        take: int,
    ) -> dict[str, Any]:
        single_provider = normalize_single_provider(provider, command="board.mine")
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self._get_azdo().get_my_work_items(
                    project=project,
                    include_closed=include_closed,
                    skip=skip,
                    take=take,
                ),
                "github": lambda: self._get_github().get_my_work_items(
                    project=project,
                    repo=repo,
                    include_closed=include_closed,
                    skip=skip,
                    take=take,
                ),
            },
        )


__all__ = [
    "SmithClient",
    "SmithError",
    "SmithAuthError",
    "SmithApiError",
]
