#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal, cast

import requests

from smith.config import (
    RemoteConfig,
    SmithConfig,
    _default_config_path,
    load_config,
    parse_runtime_config,
)
from smith.discovery import DiscoveryQuery
from smith.errors import SmithApiError, SmithAuthError, SmithError
from smith.fanout import run_fanout
from smith.http import configure_http_session
from smith.pipeline_listing import PipelineListQuery
from smith.providers.azdo import AzdoProvider
from smith.providers.base import BaseProvider
from smith.providers.github import GITHUB_DEFAULT_API_URL, GITHUB_DEFAULT_API_VERSION, GitHubProvider
from smith.providers.gitlab import GITLAB_DEFAULT_API_URL, GitLabProvider
from smith.providers.youtrack import YouTrackProvider


class SmithClient:
    def __init__(
        self,
        *,
        api_version: str | None = None,
        timeout_seconds: int | None = None,
        max_output_chars: int | None = None,
        credential: Any | None = None,
        session: requests.Session | None = None,
        smith_config: SmithConfig | None = None,
    ) -> None:
        self._config = smith_config or load_config()
        self._runtime = parse_runtime_config(
            azdo_org=None,
            api_version=api_version,
            timeout_seconds=timeout_seconds or self._config.defaults.get("timeout_seconds"),
            max_output_chars=max_output_chars or self._config.defaults.get("max_output_chars"),
            github_api_url_default=GITHUB_DEFAULT_API_URL,
            github_api_version_default=GITHUB_DEFAULT_API_VERSION,
            gitlab_api_url_default=GITLAB_DEFAULT_API_URL,
        )

        if not self._config.remotes:
            raise ValueError(
                f"No remotes configured in {_default_config_path()}. "
                "Add at least one remote under `remotes:` or run `smith config init`."
            )

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

    @staticmethod
    def _require_single_target(remote_or_provider: str, *, command: str) -> str:
        target = str(remote_or_provider or "").strip()
        if not target:
            raise ValueError(f"{command} requires a remote.")
        if target == "all":
            raise ValueError(f"{command} does not support target 'all'. Use a configured remote name.")
        return target

    def _require_gitlab_target(self, remote_or_provider: str, *, command: str) -> str:
        target = self._require_single_target(remote_or_provider, command=command)
        remotes = self._resolve_remotes(target)
        if not remotes:
            raise ValueError(f"No enabled remote found for '{target}'")
        if len(remotes) != 1 or remotes[0].provider != "gitlab":
            raise ValueError(f"{command} is only supported for GitLab remotes.")
        return target

    def _get_provider_for_remote(self, remote: RemoteConfig) -> BaseProvider:
        if remote.name in self._provider_cache:
            return self._provider_cache[remote.name]

        if remote.provider == "github":
            provider: BaseProvider = GitHubProvider(
                config=self._runtime,
                session=self._main_session,
                github_org=remote.org,
                github_api_url=remote.api_url,
                github_api_version=self._runtime.github_api_version,
                token_env=remote.token_env,
            )
        elif remote.provider == "gitlab":
            provider = GitLabProvider(
                config=self._runtime,
                session=self._main_session,
                gitlab_org=remote.org,
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
        elif remote.provider == "youtrack":
            provider = YouTrackProvider(
                config=self._runtime,
                session=self._main_session,
                youtrack_api_url=remote.api_url,
                token_env=remote.token_env,
            )
        else:
            raise ValueError(f"Unsupported provider: {remote.provider}")

        self._provider_cache[remote.name] = provider
        return provider

    def _azdo_provider(self, remote: RemoteConfig) -> AzdoProvider:
        return cast(AzdoProvider, self._get_provider_for_remote(remote))

    def _github_provider(self, remote: RemoteConfig) -> GitHubProvider:
        return cast(GitHubProvider, self._get_provider_for_remote(remote))

    def _gitlab_provider(self, remote: RemoteConfig) -> GitLabProvider:
        return cast(GitLabProvider, self._get_provider_for_remote(remote))

    def _youtrack_provider(self, remote: RemoteConfig) -> YouTrackProvider:
        return cast(YouTrackProvider, self._get_provider_for_remote(remote))

    @staticmethod
    def _remote_warnings_and_partial(payload: Any) -> tuple[list[str], bool]:
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
    def _remote_entry_success(payload: Any) -> dict[str, Any]:
        warnings, partial = SmithClient._remote_warnings_and_partial(payload)
        return {
            "ok": True,
            "data": payload,
            "warnings": warnings,
            "partial": partial,
            "error": None,
        }

    @staticmethod
    def _remote_entry_error(code: str, message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "data": None,
            "warnings": [],
            "partial": False,
            "error": {"code": code, "message": message},
        }

    def _resolve_remotes(self, remote_or_provider: str) -> list[RemoteConfig]:
        if remote_or_provider == "all":
            return [r for r in self._config.remotes.values() if r.enabled]

        if remote_or_provider in self._config.remotes:
            remote = self._config.remotes[remote_or_provider]
            return [remote] if remote.enabled else []

        return []

    def _fanout(
        self,
        *,
        remote_or_provider: str,
        operations: dict[str, Callable[[RemoteConfig], Any]],
    ) -> dict[str, Any]:
        remotes = self._resolve_remotes(remote_or_provider)
        if not remotes:
            raise ValueError(f"No enabled remote found for '{remote_or_provider}'")
        remotes = [remote for remote in remotes if remote.provider in operations]
        if not remotes:
            raise ValueError(f"No enabled remote found for '{remote_or_provider}'")

        remote_operations: dict[str, Callable[[], Any]] = {}

        def _make_operation(remote: RemoteConfig) -> Callable[[], Any]:
            def _operation() -> Any:
                return operations[remote.provider](remote)

            return _operation

        for remote in remotes:
            remote_operations[remote.name] = _make_operation(remote)

        return run_fanout(
            remotes=[r.name for r in remotes],
            requested_remote=remote_or_provider,
            operations=remote_operations,
            remote_entry_success=self._remote_entry_success,
            remote_entry_error=self._remote_entry_error,
        )

    @staticmethod
    def _annotate_azdo_repositories(repositories: Any, *, project: str) -> list[Any]:
        if isinstance(repositories, list):
            items = repositories
        elif repositories is None:
            items = []
        else:
            items = [repositories]

        annotated: list[Any] = []
        for repository in items:
            if isinstance(repository, dict):
                repository_entry = dict(repository)
                repository_entry.setdefault("projectName", project)
                annotated.append(repository_entry)
            else:
                annotated.append(repository)
        return annotated

    def _list_azdo_repositories(self, *, azdo: AzdoProvider, project: str | None) -> list[Any]:
        if project:
            return self._annotate_azdo_repositories(
                azdo.list_repositories(project=project),
                project=project,
            )

        repositories: list[Any] = []
        for project_entry in azdo.list_projects():
            if not isinstance(project_entry, dict):
                continue
            project_name = str(project_entry.get("name", "") or "").strip()
            if not project_name:
                continue
            repositories.extend(
                self._annotate_azdo_repositories(
                    azdo.list_repositories(project=project_name),
                    project=project_name,
                )
            )
        return repositories

    @staticmethod
    def _cache_clean_roots(
        *,
        remote: str,
        smith_config: SmithConfig | None = None,
    ) -> list[str]:
        target = str(remote or "all").strip()
        if not target or target == "all":
            providers = ["github", "gitlab"]
        else:
            config = smith_config or load_config()
            remote_config = config.remotes.get(target)
            if remote_config is None:
                raise ValueError(f"Unknown remote '{target}'")
            providers = [remote_config.provider]

        roots: list[str] = []
        if "github" in providers:
            roots.append(os.getenv("SMITH_GITHUB_GREP_CACHE_DIR") or str(Path.home() / ".cache" / "smith" / "github-grep"))
        if "gitlab" in providers:
            roots.append(os.getenv("SMITH_GITLAB_GREP_CACHE_DIR") or str(Path.home() / ".cache" / "smith" / "gitlab-grep"))
        return roots

    @staticmethod
    def execute_cache_clean(
        *,
        remote: str,
        smith_config: SmithConfig | None = None,
    ) -> dict[str, Any]:
        cleaned: list[str] = []
        missing: list[str] = []

        for root in SmithClient._cache_clean_roots(remote=remote, smith_config=smith_config):
            path = Path(root)
            if path.exists():
                shutil.rmtree(path)
                cleaned.append(str(path))
            else:
                missing.append(str(path))

        return {"cleaned": cleaned, "missing": missing}

    def execute_discover_projects(self, *, remote_or_provider: str) -> dict[str, Any]:
        target = self._require_single_target(remote_or_provider, command="orgs")
        return self._fanout(
            remote_or_provider=target,
            operations={
                "azdo": lambda r: self._azdo_provider(r).list_projects(),
                "github": lambda r: self._github_provider(r).list_projects(),
                "gitlab": lambda r: self._gitlab_provider(r).list_projects(),
            },
        )

    def execute_discover_repos(
        self,
        *,
        remote_or_provider: str,
        project: str | None,
        group: str | None = None,
        grep: str | None = None,
        skip: int = 0,
        take: int | None = None,
    ) -> dict[str, Any]:
        target = self._require_single_target(remote_or_provider, command="repos")
        return self._fanout(
            remote_or_provider=target,
            operations={
                "azdo": lambda r: self._list_azdo_repositories(azdo=self._azdo_provider(r), project=project),
                "github": lambda r: self._github_provider(r).list_repositories(),
                "gitlab": lambda r: self._gitlab_provider(r).discover_repositories(
                    group=group,
                    query=DiscoveryQuery.create(grep=grep, skip=skip, take=take),
                ),
            },
        )

    def execute_list_groups(
        self,
        *,
        remote_or_provider: str,
        grep: str | None = None,
        skip: int = 0,
        take: int | None = None,
    ) -> dict[str, Any]:
        target = self._require_single_target(remote_or_provider, command="groups")
        return self._fanout(
            remote_or_provider=target,
            operations={
                "gitlab": lambda r: self._gitlab_provider(r).discover_groups(
                    query=DiscoveryQuery.create(grep=grep, skip=skip, take=take),
                ),
            },
        )

    def execute_code_search(
        self,
        *,
        remote_or_provider: str,
        query: str,
        project: str | None,
        repos: list[str] | None,
        skip: int,
        take: int,
    ) -> dict[str, Any]:
        return self._fanout(
            remote_or_provider=remote_or_provider,
            operations={
                "azdo": lambda r: self._azdo_provider(r).search_code(
                    query=query,
                    project=project,
                    repos=repos,
                    skip=skip,
                    take=take,
                ),
                "github": lambda r: self._github_provider(r).search_code(
                    query=query,
                    project=project,
                    repos=repos,
                    skip=skip,
                    take=take,
                ),
                "gitlab": lambda r: self._gitlab_provider(r).search_code(
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
        remote_or_provider: str,
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
        reverse: bool = False,
    ) -> dict[str, Any]:
        target = self._require_single_target(remote_or_provider, command="code.grep")
        return self._fanout(
            remote_or_provider=target,
            operations={
                "azdo": lambda r: self._azdo_provider(r).grep(
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
                    reverse=reverse,
                    no_clone=no_clone,
                ),
                "github": lambda r: self._github_provider(r).grep(
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
                    reverse=reverse,
                    no_clone=no_clone,
                ),
                "gitlab": lambda r: self._gitlab_provider(r).grep(
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
                    reverse=reverse,
                    no_clone=no_clone,
                ),
            },
        )

    def execute_pr_search(
        self,
        *,
        remote_or_provider: str,
        query: str,
        project: str | None,
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
        return self._fanout(
            remote_or_provider=remote_or_provider,
            operations={
                "azdo": lambda r: self._azdo_provider(r).search_pull_requests(
                    query=query,
                    project=project,
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
                "github": lambda r: self._github_provider(r).search_pull_requests(
                    query=query,
                    project=project,
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
                "gitlab": lambda r: self._gitlab_provider(r).search_pull_requests(
                    query=query,
                    project=project,
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
            },
        )

    def execute_pr_list(
        self,
        *,
        remote_or_provider: str,
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
        target = self._require_single_target(remote_or_provider, command="prs.list")
        return self._fanout(
            remote_or_provider=target,
            operations={
                "azdo": lambda r: self._azdo_provider(r).list_pull_requests(
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
                "github": lambda r: self._github_provider(r).list_pull_requests(
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
                "gitlab": lambda r: self._gitlab_provider(r).list_pull_requests(
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
        remote_or_provider: str,
        project: str | None,
        repo: str,
        pull_request_id: int,
    ) -> dict[str, Any]:
        target = self._require_single_target(remote_or_provider, command="prs.get")
        return self._fanout(
            remote_or_provider=target,
            operations={
                "azdo": lambda r: self._azdo_provider(r).get_pull_request(
                    project=str(project),
                    repo=repo,
                    pull_request_id=pull_request_id,
                ),
                "github": lambda r: self._github_provider(r).get_pull_request(
                    repo=repo,
                    pull_request_id=pull_request_id,
                ),
                "gitlab": lambda r: self._gitlab_provider(r).get_pull_request(
                    repo=repo,
                    pull_request_id=pull_request_id,
                ),
            },
        )

    def execute_pr_threads(
        self,
        *,
        remote_or_provider: str,
        project: str | None,
        repo: str,
        pull_request_id: int,
    ) -> dict[str, Any]:
        target = self._require_single_target(remote_or_provider, command="prs.threads")
        return self._fanout(
            remote_or_provider=target,
            operations={
                "azdo": lambda r: self._azdo_provider(r).get_pull_request_threads(
                    project=str(project),
                    repo=repo,
                    pull_request_id=pull_request_id,
                ),
                "github": lambda r: self._github_provider(r).get_pull_request_threads(
                    repo=repo,
                    pull_request_id=pull_request_id,
                ),
                "gitlab": lambda r: self._gitlab_provider(r).get_pull_request_threads(
                    repo=repo,
                    pull_request_id=pull_request_id,
                ),
            },
        )

    def execute_ci_list(
        self,
        *,
        remote_or_provider: str,
        project: str | None,
        repo: str | None,
        pipeline_id: int,
        grep: str | None = None,
        statuses: list[str] | None = None,
        skip: int = 0,
        take: int | None = None,
        max_depth: int = 0,
    ) -> dict[str, Any]:
        target = self._require_single_target(remote_or_provider, command="pipelines.list")
        effective_repo = repo or project
        query = PipelineListQuery.create(
            grep=grep,
            statuses=statuses,
            skip=skip,
            take=take,
            max_depth=max_depth,
        )
        return self._fanout(
            remote_or_provider=target,
            operations={
                "azdo": lambda r: self._azdo_provider(r).list_pipelines(
                    project=str(project),
                    pipeline_id=pipeline_id,
                    query=query,
                ),
                "github": lambda r: self._github_provider(r).list_pipelines(
                    repo=str(effective_repo),
                    pipeline_id=pipeline_id,
                    query=query,
                ),
                "gitlab": lambda r: self._gitlab_provider(r).list_pipelines(
                    repo=str(effective_repo),
                    pipeline_id=pipeline_id,
                    query=query,
                ),
            },
        )

    def execute_ci_grep(
        self,
        *,
        remote_or_provider: str,
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
        reverse: bool = False,
    ) -> dict[str, Any]:
        target = self._require_single_target(remote_or_provider, command="pipelines.grep")
        effective_repo = repo or project
        return self._fanout(
            remote_or_provider=target,
            operations={
                "azdo": lambda r: self._azdo_provider(r).grep_build_log(
                    project=str(project),
                    build_id=build_id,
                    log_id=log_id,
                    pattern=pattern,
                    output_mode=output_mode,
                    case_insensitive=case_insensitive,
                    context_lines=context_lines,
                    from_line=from_line,
                    to_line=to_line,
                    reverse=reverse,
                ),
                "github": lambda r: self._github_provider(r).grep_build_log(
                    repo=str(effective_repo),
                    build_id=build_id,
                    log_id=log_id,
                    pattern=pattern,
                    output_mode=output_mode,
                    case_insensitive=case_insensitive,
                    context_lines=context_lines,
                    from_line=from_line,
                    to_line=to_line,
                    reverse=reverse,
                ),
                "gitlab": lambda r: self._gitlab_provider(r).grep_build_log(
                    repo=str(effective_repo),
                    build_id=build_id,
                    log_id=log_id,
                    pattern=pattern,
                    output_mode=output_mode,
                    case_insensitive=case_insensitive,
                    context_lines=context_lines,
                    from_line=from_line,
                    to_line=to_line,
                    reverse=reverse,
                ),
            },
        )

    def execute_ci_artifacts_list(
        self,
        *,
        remote_or_provider: str,
        project: str | None,
        repo: str | None,
        pipeline_id: int,
        job_id: int,
    ) -> dict[str, Any]:
        target = self._require_gitlab_target(
            remote_or_provider,
            command="pipelines.artifacts.list",
        )
        effective_repo = repo or project
        return self._fanout(
            remote_or_provider=target,
            operations={
                "gitlab": lambda r: self._gitlab_provider(r).list_job_artifacts(
                    repo=str(effective_repo),
                    pipeline_id=pipeline_id,
                    job_id=job_id,
                ),
            },
        )

    def execute_ci_artifacts_grep(
        self,
        *,
        remote_or_provider: str,
        project: str | None,
        repo: str | None,
        pipeline_id: int,
        job_id: int,
        pattern: str | None,
        path: str | None,
        glob: str | None,
        output_mode: Literal["content", "files_with_matches", "count"],
        case_insensitive: bool,
        context_lines: int | None,
        from_line: int | None,
        to_line: int | None,
        reverse: bool = False,
    ) -> dict[str, Any]:
        target = self._require_gitlab_target(
            remote_or_provider,
            command="pipelines.artifacts.grep",
        )
        effective_repo = repo or project
        return self._fanout(
            remote_or_provider=target,
            operations={
                "gitlab": lambda r: self._gitlab_provider(r).grep_job_artifacts(
                    repo=str(effective_repo),
                    pipeline_id=pipeline_id,
                    job_id=job_id,
                    pattern=pattern,
                    path=path,
                    glob=glob,
                    output_mode=output_mode,
                    case_insensitive=case_insensitive,
                    context_lines=context_lines,
                    from_line=from_line,
                    to_line=to_line,
                    reverse=reverse,
                ),
            },
        )

    def execute_work_get(
        self,
        *,
        remote_or_provider: str,
        project: str | None,
        repo: str | None,
        work_item_id: int | str,
    ) -> dict[str, Any]:
        target = self._require_single_target(remote_or_provider, command="stories.get")
        effective_repo = repo or project

        def _numeric_work_item_id() -> int:
            if isinstance(work_item_id, int):
                return work_item_id
            return int(work_item_id)

        return self._fanout(
            remote_or_provider=target,
            operations={
                "azdo": lambda r: self._azdo_provider(r).get_ticket_by_id(
                    project=str(project),
                    work_item_id=_numeric_work_item_id(),
                ),
                "github": lambda r: self._github_provider(r).get_ticket_by_id(
                    repo=str(effective_repo),
                    work_item_id=_numeric_work_item_id(),
                ),
                "gitlab": lambda r: self._gitlab_provider(r).get_ticket_by_id(
                    repo=str(effective_repo),
                    work_item_id=_numeric_work_item_id(),
                ),
                "youtrack": lambda r: self._youtrack_provider(r).get_ticket_by_id(
                    work_item_id=work_item_id,
                ),
            },
        )

    def execute_work_search(
        self,
        *,
        remote_or_provider: str,
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
        target = self._require_single_target(remote_or_provider, command="stories.search")
        return self._fanout(
            remote_or_provider=target,
            operations={
                "azdo": lambda r: self._azdo_provider(r).search_work_items(
                    query=query,
                    project=project,
                    area=area,
                    work_item_type=work_item_type,
                    state=state,
                    assigned_to=assigned_to,
                    skip=skip,
                    take=take,
                ),
                "github": lambda r: self._github_provider(r).search_work_items(
                    query=query,
                    project=project,
                    repo=repo,
                    state=state,
                    assigned_to=assigned_to,
                    skip=skip,
                    take=take,
                    include_closed=True,
                ),
                "gitlab": lambda r: self._gitlab_provider(r).search_work_items(
                    query=query,
                    project=project,
                    repo=repo,
                    state=state,
                    assigned_to=assigned_to,
                    skip=skip,
                    take=take,
                    include_closed=True,
                ),
                "youtrack": lambda r: self._youtrack_provider(r).search_work_items(
                    query=query,
                    project=project,
                    repo=repo,
                    state=state,
                    assigned_to=assigned_to,
                    work_item_type=work_item_type,
                    skip=skip,
                    take=take,
                    include_closed=True,
                ),
            },
        )

    def execute_work_mine(
        self,
        *,
        remote_or_provider: str,
        project: str | None,
        repo: str | None,
        include_closed: bool,
        skip: int,
        take: int,
    ) -> dict[str, Any]:
        target = self._require_single_target(remote_or_provider, command="stories.mine")
        return self._fanout(
            remote_or_provider=target,
            operations={
                "azdo": lambda r: self._azdo_provider(r).get_my_work_items(
                    project=project,
                    include_closed=include_closed,
                    skip=skip,
                    take=take,
                ),
                "github": lambda r: self._github_provider(r).get_my_work_items(
                    project=project,
                    repo=repo,
                    include_closed=include_closed,
                    skip=skip,
                    take=take,
                ),
                "gitlab": lambda r: self._gitlab_provider(r).get_my_work_items(
                    project=project,
                    repo=repo,
                    include_closed=include_closed,
                    skip=skip,
                    take=take,
                ),
                "youtrack": lambda r: self._youtrack_provider(r).get_my_work_items(
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
