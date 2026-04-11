#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal

import requests

from smith.config import RemoteConfig, SmithConfig, load_config, parse_runtime_config
from smith.errors import SmithApiError, SmithAuthError, SmithError
from smith.fanout import run_fanout
from smith.http import configure_http_session
from smith.providers.azdo import AzdoProvider
from smith.providers.base import BaseProvider, normalize_single_provider
from smith.providers.github import GITHUB_DEFAULT_API_URL, GITHUB_DEFAULT_API_VERSION, GitHubProvider
from smith.providers.gitlab import GITLAB_DEFAULT_API_URL, GitLabProvider

_NO_PROVIDERS_CONFIGURED_MESSAGE = (
    "No remotes configured. Create a config file at ~/.config/smith/config.yaml "
    "or set at least one of:\n"
    "  - AZURE_DEVOPS_ORG (for Azure DevOps)\n"
    "  - GITHUB_ORG (for GitHub)\n"
    "  - GITLAB_GROUP (for GitLab)"
)


class SmithClient:
    def __init__(
        self,
        *,
        azdo_org: str | None = None,
        github_org: str | None = None,
        gitlab_group: str | None = None,
        api_version: str | None = None,
        timeout_seconds: int | None = None,
        max_output_chars: int | None = None,
        credential: Any | None = None,
        session: requests.Session | None = None,
        smith_config: SmithConfig | None = None,
    ) -> None:
        if smith_config is not None:
            self._config = smith_config
        elif azdo_org or github_org or gitlab_group:
            runtime = parse_runtime_config(
                azdo_org=azdo_org,
                api_version=api_version,
                timeout_seconds=timeout_seconds,
                max_output_chars=max_output_chars,
                github_org=github_org,
                github_api_url_default=GITHUB_DEFAULT_API_URL,
                github_api_version_default=GITHUB_DEFAULT_API_VERSION,
                gitlab_group=gitlab_group,
                gitlab_api_url_default=GITLAB_DEFAULT_API_URL,
            )
            remotes: dict[str, RemoteConfig] = {}
            if runtime.github_configured:
                remotes["github"] = RemoteConfig(
                    name="github",
                    provider="github",
                    org=runtime.github_org,
                    host="github.com",
                    token_env="GITHUB_TOKEN",
                    enabled=True,
                    api_url=runtime.github_api_url,
                )
            if runtime.gitlab_configured:
                remotes["gitlab"] = RemoteConfig(
                    name="gitlab",
                    provider="gitlab",
                    org=runtime.gitlab_group,
                    host="gitlab.com",
                    token_env="GITLAB_TOKEN",
                    enabled=True,
                    api_url=runtime.gitlab_api_url,
                )
            if runtime.azdo_configured:
                remotes["azdo"] = RemoteConfig(
                    name="azdo",
                    provider="azdo",
                    org=runtime.azdo_org,
                    host="dev.azure.com",
                    token_env="AZURE_DEVOPS_PAT",
                    enabled=True,
                    api_url="https://dev.azure.com",
                )
            self._config = SmithConfig(remotes=remotes, defaults={})
            self._runtime = runtime
        else:
            self._config = load_config()
            self._runtime = parse_runtime_config(
                azdo_org=None,
                api_version=api_version,
                timeout_seconds=timeout_seconds or self._config.defaults.get("timeout_seconds"),
                max_output_chars=max_output_chars or self._config.defaults.get("max_output_chars"),
                github_org=None,
                github_api_url_default=GITHUB_DEFAULT_API_URL,
                github_api_version_default=GITHUB_DEFAULT_API_VERSION,
                gitlab_group=None,
                gitlab_api_url_default=GITLAB_DEFAULT_API_URL,
            )

        if not self._config.remotes:
            raise ValueError(_NO_PROVIDERS_CONFIGURED_MESSAGE)

        main_session = session or requests.Session()
        configure_http_session(
            main_session,
            pool_connections=self._runtime.http_pool_connections,
            pool_maxsize=self._runtime.http_pool_maxsize,
        )

        self._credential = credential
        self._main_session = main_session
        self._provider_cache: dict[str, BaseProvider] = {}

        self.api_version = self._runtime.api_version
        self.timeout_seconds = self._runtime.timeout_seconds
        self.max_output_chars = self._runtime.max_output_chars

    def _get_provider_for_remote(self, remote: RemoteConfig) -> BaseProvider:
        if remote.name in self._provider_cache:
            return self._provider_cache[remote.name]

        if remote.provider == "github":
            provider: BaseProvider = GitHubProvider(
                config=self._runtime,
                session=self._main_session,
                github_org=remote.org,
                github_api_url=remote.api_url,
                github_api_version=GITHUB_DEFAULT_API_VERSION,
                token_env=remote.token_env,
            )
        elif remote.provider == "gitlab":
            provider = GitLabProvider(
                config=self._runtime,
                session=self._main_session,
                gitlab_group=remote.org,
                gitlab_api_url=remote.api_url,
                token_env=remote.token_env,
            )
        elif remote.provider == "azdo":
            provider = AzdoProvider(
                config=self._runtime,
                credential=self._credential,
                session=self._main_session,
                azdo_org=remote.org,
                token_env=remote.token_env,
            )
        else:
            raise ValueError(f"Unsupported provider: {remote.provider}")

        self._provider_cache[remote.name] = provider
        return provider

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

    def _resolve_remotes(self, remote_or_provider: str) -> list[RemoteConfig]:
        if remote_or_provider in self._config.remotes:
            remote = self._config.remotes[remote_or_provider]
            return [remote] if remote.enabled else []

        if remote_or_provider in {"github", "gitlab", "azdo"}:
            return [
                r for r in self._config.remotes.values()
                if r.provider == remote_or_provider and r.enabled
            ]

        if remote_or_provider == "all":
            return [r for r in self._config.remotes.values() if r.enabled]

        return []

    def _fanout(
        self,
        *,
        remote_or_provider: str,
        operations: dict[str, Callable[[RemoteConfig], Any]],
    ) -> dict[str, Any]:
        remotes = self._resolve_remotes(remote_or_provider)
        if not remotes:
            raise ValueError(f"No enabled remotes found for '{remote_or_provider}'")

        remote_operations: dict[str, Callable[[], Any]] = {}
        for remote in remotes:
            remote_operations[remote.name] = lambda r=remote: operations[r.provider](r)

        return run_fanout(
            providers=[r.name for r in remotes],
            requested_provider=remote_or_provider,
            operations=remote_operations,
            provider_entry_success=self._provider_entry_success,
            provider_entry_error=self._provider_entry_error,
        )

    @staticmethod
    def _github_grep_cache_root() -> str:
        configured = (os.getenv("SMITH_GITHUB_GREP_CACHE_DIR") or "").strip()
        if configured:
            return configured
        return str(Path.home() / ".cache" / "smith" / "github-grep")

    @staticmethod
    def _gitlab_grep_cache_root() -> str:
        configured = (os.getenv("SMITH_GITLAB_GREP_CACHE_DIR") or "").strip()
        if configured:
            return configured
        return str(Path.home() / ".cache" / "smith" / "gitlab-grep")

    @classmethod
    def _cache_clean_roots(cls, *, provider: str | None) -> list[str]:
        normalized = (provider or "all").strip().lower()
        if normalized not in {"all", "github", "gitlab"}:
            raise ValueError("cache clean supports provider values: github, gitlab, all")

        roots: list[str] = []
        if normalized in {"all", "github"}:
            roots.append(cls._github_grep_cache_root())
        if normalized in {"all", "gitlab"}:
            roots.append(cls._gitlab_grep_cache_root())
        return roots

    @classmethod
    def execute_cache_clean(cls, *, provider: str | None = None) -> dict[str, Any]:
        cleaned: list[str] = []
        missing: list[str] = []

        for cache_root in cls._cache_clean_roots(provider=provider):
            if os.path.isdir(cache_root):
                shutil.rmtree(cache_root)
                cleaned.append(cache_root)
                continue
            if os.path.exists(cache_root):
                os.remove(cache_root)
                cleaned.append(cache_root)
                continue
            missing.append(cache_root)

        return {
            "cleaned": cleaned,
            "missing": missing,
        }

    def _list_azdo_repositories(self, *, azdo: AzdoProvider, project: str | None) -> list[dict[str, Any]]:
        def _normalize_repo_rows(payload: Any) -> list[dict[str, Any]]:
            if isinstance(payload, dict):
                return [payload]
            if isinstance(payload, list):
                return [row for row in payload if isinstance(row, dict)]
            return []

        if project:
            repos = _normalize_repo_rows(azdo.list_repositories(project=project))
            return [{**repo, "projectName": project} for repo in repos]

        results: list[dict[str, Any]] = []
        for project_row in azdo.list_projects():
            project_name = str(project_row.get("name") or "").strip()
            if not project_name:
                continue
            repos = _normalize_repo_rows(azdo.list_repositories(project=project_name))
            results.extend({**repo, "projectName": project_name} for repo in repos)
        return results

    def execute_discover_projects(self, *, provider: str) -> dict[str, Any]:
        single_provider = normalize_single_provider(provider, command="orgs")
        return self._fanout(
            remote_or_remote_or_provider=single_provider,
            operations={
                "azdo": lambda r: self._get_provider_for_remote(r).list_projects(),
                "github": lambda r: self._get_provider_for_remote(r).list_projects(),
                "gitlab": lambda r: self._get_provider_for_remote(r).list_projects(),
            },
        )

    def execute_discover_repos(
        self,
        *,
        provider: str,
        project: str | None,
    ) -> dict[str, Any]:
        single_provider = normalize_single_provider(provider, command="repos")
        return self._fanout(
            remote_or_remote_or_provider=single_provider,
            operations={
                "azdo": lambda r: self._list_azdo_repositories(
                    azdo=self._get_provider_for_remote(r),
                    project=project,
                ),
                "github": lambda r: self._get_provider_for_remote(r).list_repositories(),
                "gitlab": lambda r: self._get_provider_for_remote(r).list_repositories(),
            },
        )

    def execute_projects_list(self, *, provider: str) -> dict[str, Any]:
        return self.execute_discover_projects(provider=provider)

    def execute_repos_list(
        self,
        *,
        provider: str,
        project: str | None,
    ) -> dict[str, Any]:
        return self.execute_discover_repos(remote_or_provider=provider, project=project)

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
            remote_or_remote_or_provider=provider,
            operations={
                "azdo": lambda r: self._get_provider_for_remote(r).search_code(
                    query=query,
                    project=project,
                    repos=repos,
                    skip=skip,
                    take=take,
                ),
                "github": lambda r: self._get_provider_for_remote(r).search_code(
                    query=query,
                    project=project,
                    repos=repos,
                    skip=skip,
                    take=take,
                ),
                "gitlab": lambda r: self._get_provider_for_remote(r).search_code(
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
        no_clone: bool,
    ) -> dict[str, Any]:
        single_provider = normalize_single_provider(provider, command="code.grep")
        return self._fanout(
            remote_or_remote_or_provider=single_provider,
            operations={
                "azdo": lambda r: self._get_provider_for_remote(r).grep(
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
                    no_clone=no_clone,
                ),
                "github": lambda r: self._get_provider_for_remote(r).grep(
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
                    no_clone=no_clone,
                ),
                "gitlab": lambda r: self._get_provider_for_remote(r).grep(
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
                    no_clone=no_clone,
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
        single_provider = normalize_single_provider(provider, command="prs.list")
        return self._fanout(
            remote_or_provider=single_provider,
            operations={
                "azdo": lambda r: self._get_provider_for_remote(r).list_pull_requests(
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
                "github": lambda r: self._get_provider_for_remote(r).list_pull_requests(
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
                "gitlab": lambda r: self._get_provider_for_remote(r).list_pull_requests(
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
        single_provider = normalize_single_provider(provider, command="prs.get")
        return self._fanout(
            remote_or_provider=single_provider,
            operations={
                "azdo": lambda r: self._get_provider_for_remote(r).get_pull_request(
                    project=str(project),
                    repo=repo,
                    pull_request_id=pull_request_id,
                ),
                "github": lambda r: self._get_provider_for_remote(r).get_pull_request(
                    repo=repo,
                    pull_request_id=pull_request_id,
                ),
                "gitlab": lambda r: self._get_provider_for_remote(r).get_pull_request(
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
        single_provider = normalize_single_provider(provider, command="prs.threads")
        return self._fanout(
            remote_or_provider=single_provider,
            operations={
                "azdo": lambda r: self._get_provider_for_remote(r).get_pull_request_threads(
                    project=str(project),
                    repo=repo,
                    pull_request_id=pull_request_id,
                ),
                "github": lambda r: self._get_provider_for_remote(r).get_pull_request_threads(
                    repo=repo,
                    pull_request_id=pull_request_id,
                ),
                "gitlab": lambda r: self._get_provider_for_remote(r).get_pull_request_threads(
                    repo=repo,
                    pull_request_id=pull_request_id,
                ),
            },
        )

    def execute_ci_logs(
        self,
        *,
        provider: str,
        project: str | None,
        repo: str | None,
        build_id: int,
    ) -> dict[str, Any]:
        single_provider = normalize_single_provider(provider, command="pipelines.logs.list")
        effective_repo = repo or project
        return self._fanout(
            remote_or_provider=single_provider,
            operations={
                "azdo": lambda r: self._get_provider_for_remote(r).get_build_log(project=str(project), build_id=build_id),
                "github": lambda r: self._get_provider_for_remote(r).get_build_log(repo=str(effective_repo), build_id=build_id),
                "gitlab": lambda r: self._get_provider_for_remote(r).get_build_log(repo=str(effective_repo), build_id=build_id),
            },
        )

    def execute_ci_grep(
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
        single_provider = normalize_single_provider(provider, command="pipelines.logs.grep")
        effective_repo = repo or project
        return self._fanout(
            remote_or_provider=single_provider,
            operations={
                "azdo": lambda r: self._get_provider_for_remote(r).grep_build_log(
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
                "github": lambda r: self._get_provider_for_remote(r).grep_build_log(
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
                "gitlab": lambda r: self._get_provider_for_remote(r).grep_build_log(
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

    def execute_build_logs(
        self,
        *,
        provider: str,
        project: str | None,
        repo: str | None,
        build_id: int,
    ) -> dict[str, Any]:
        return self.execute_ci_logs(
            remote_or_provider=provider,
            project=project,
            repo=repo,
            build_id=build_id,
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
        return self.execute_ci_grep(
            remote_or_provider=provider,
            project=project,
            repo=repo,
            build_id=build_id,
            log_id=log_id,
            pattern=pattern,
            output_mode=output_mode,
            case_insensitive=case_insensitive,
            context_lines=context_lines,
            from_line=from_line,
            to_line=to_line,
        )

    def execute_work_get(
        self,
        *,
        provider: str,
        project: str | None,
        repo: str | None,
        work_item_id: int,
    ) -> dict[str, Any]:
        single_provider = normalize_single_provider(provider, command="stories.get")
        effective_repo = repo or project
        return self._fanout(
            remote_or_provider=single_provider,
            operations={
                "azdo": lambda r: self._get_provider_for_remote(r).get_ticket_by_id(project=str(project), work_item_id=work_item_id),
                "github": lambda r: self._get_provider_for_remote(r).get_ticket_by_id(repo=str(effective_repo), work_item_id=work_item_id),
                "gitlab": lambda r: self._get_provider_for_remote(r).get_ticket_by_id(repo=str(effective_repo), work_item_id=work_item_id),
            },
        )

    def execute_work_search(
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
        single_provider = normalize_single_provider(provider, command="stories.search")
        return self._fanout(
            remote_or_provider=single_provider,
            operations={
                "azdo": lambda r: self._get_provider_for_remote(r).search_work_items(
                    query=query,
                    project=project,
                    area=area,
                    work_item_type=work_item_type,
                    state=state,
                    assigned_to=assigned_to,
                    skip=skip,
                    take=take,
                ),
                "github": lambda r: self._get_provider_for_remote(r).search_work_items(
                    query=query,
                    project=project,
                    repo=repo,
                    state=state,
                    assigned_to=assigned_to,
                    skip=skip,
                    take=take,
                    include_closed=True,
                ),
                "gitlab": lambda r: self._get_provider_for_remote(r).search_work_items(
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

    def execute_work_mine(
        self,
        *,
        provider: str,
        project: str | None,
        repo: str | None,
        include_closed: bool,
        skip: int,
        take: int,
    ) -> dict[str, Any]:
        single_provider = normalize_single_provider(provider, command="stories.mine")
        return self._fanout(
            remote_or_provider=single_provider,
            operations={
                "azdo": lambda r: self._get_provider_for_remote(r).get_my_work_items(
                    project=project,
                    include_closed=include_closed,
                    skip=skip,
                    take=take,
                ),
                "github": lambda r: self._get_provider_for_remote(r).get_my_work_items(
                    project=project,
                    repo=repo,
                    include_closed=include_closed,
                    skip=skip,
                    take=take,
                ),
                "gitlab": lambda r: self._get_provider_for_remote(r).get_my_work_items(
                    project=project,
                    repo=repo,
                    include_closed=include_closed,
                    skip=skip,
                    take=take,
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
        return self.execute_work_get(
            remote_or_provider=provider,
            project=project,
            repo=repo,
            work_item_id=work_item_id,
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
        return self.execute_work_search(
            remote_or_provider=provider,
            query=query,
            project=project,
            repo=repo,
            area=area,
            work_item_type=work_item_type,
            state=state,
            assigned_to=assigned_to,
            skip=skip,
            take=take,
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
        return self.execute_work_mine(
            remote_or_provider=provider,
            project=project,
            repo=repo,
            include_closed=include_closed,
            skip=skip,
            take=take,
        )


__all__ = [
    "SmithClient",
    "SmithError",
    "SmithAuthError",
    "SmithApiError",
]
