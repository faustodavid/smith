from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Literal, Protocol
from urllib.parse import quote

from smith.config import parse_bool_env, parse_int_env
from smith.discovery import DiscoveryQuery, build_discovery_payload
from smith.errors import SmithApiError
from smith.formatting import glob_to_regex, normalize_branch_name, truncate_output
from smith.providers import local_checkout as _local_checkout
from smith.providers.helpers import (
    build_grep_result,
    grep_compile_error_result,
    grep_match_lines,
    grep_too_many_files_result,
)
from smith.utils import compile_search_pattern, match_all_pattern, normalize_path, slice_lines

logger = logging.getLogger(__name__)

_CACHE_LOCKS: dict[str, threading.Lock] = {}
_CACHE_LOCKS_GUARD = threading.Lock()


class _GitLabDiscoveryProvider(Protocol):
    _group_list_cache: list[dict[str, Any]] | None
    _repository_list_cache: dict[str, list[dict[str, Any]]]

    def _gitlab_web_url(self) -> str: ...
    def _cache_project(
        self,
        *,
        project_id: str | None,
        full_path: str,
        relative_path: str | None = None,
        default_branch: str | None = None,
    ) -> None: ...
    def _get_paginated_list(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]: ...
    def _get_paginated_page(
        self,
        path: str,
        *,
        params: dict[str, Any] | None,
        page: int,
        per_page: int,
    ) -> tuple[list[dict[str, Any]], int | None]: ...
    def _map_group_entry(self, item: dict[str, Any]) -> dict[str, Any] | None: ...
    def _map_repository_entry(self, item: dict[str, Any]) -> dict[str, Any] | None: ...
    def _slice_discovery_rows(
        self,
        *,
        rows: list[dict[str, Any]],
        query: DiscoveryQuery,
        subject: str,
    ) -> dict[str, Any]: ...
    def _discover_paginated_rows(
        self,
        *,
        cached_rows: list[dict[str, Any]] | None,
        query: DiscoveryQuery,
        subject: str,
        path: str,
        params: dict[str, Any],
        search_term: str | None,
        mapper: Callable[[dict[str, Any]], dict[str, Any] | None],
        store_rows: Callable[[list[dict[str, Any]]], None],
    ) -> dict[str, Any]: ...
    def _load_group_rows(self, *, search: str | None = None) -> list[dict[str, Any]]: ...
    def _load_repository_rows(
        self,
        *,
        group: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]: ...


class GitLabCodeMixin:
    _group_list_cache: list[dict[str, Any]] | None
    _repository_list_cache: dict[str, list[dict[str, Any]]]

    def list_projects(self: Any) -> list[dict[str, Any]]:
        return self.list_groups()

    @staticmethod
    def _matches_discovery_name(row: dict[str, Any], pattern: re.Pattern[str] | None) -> bool:
        if pattern is None:
            return True
        name = str(row.get("name", "") or "").strip()
        return bool(name) and pattern.search(name) is not None

    def _map_group_entry(self: _GitLabDiscoveryProvider, item: dict[str, Any]) -> dict[str, Any] | None:
        full_path = str(item.get("full_path") or item.get("path") or "").strip().strip("/")
        if not full_path:
            return None
        return {
            "id": item.get("id"),
            "name": full_path,
            "state": "active",
            "url": item.get("web_url") or f"{self._gitlab_web_url()}/{full_path}",
        }

    def _map_repository_entry(self: _GitLabDiscoveryProvider, item: dict[str, Any]) -> dict[str, Any] | None:
        full_path = str(item.get("path_with_namespace") or "").strip().strip("/")
        if not full_path:
            return None
        self._cache_project(
            project_id=str(item.get("id") or "") or None,
            full_path=full_path,
            default_branch=str(item.get("default_branch") or "") or None,
        )
        return {
            "id": item.get("id"),
            "name": full_path,
            "defaultBranch": item.get("default_branch"),
            "webUrl": item.get("web_url"),
        }

    def _slice_discovery_rows(
        self: _GitLabDiscoveryProvider,
        *,
        rows: list[dict[str, Any]],
        query: DiscoveryQuery,
        subject: str,
    ) -> dict[str, Any]:
        pattern = query.compile_grep()
        matched = [row for row in rows if GitLabCodeMixin._matches_discovery_name(row, pattern)]
        window_end = query.skip + query.take
        return build_discovery_payload(
            rows=matched[query.skip:window_end],
            query=query,
            has_more=len(matched) > window_end,
            subject=subject,
        )

    def _discover_paginated_rows(
        self: _GitLabDiscoveryProvider,
        *,
        cached_rows: list[dict[str, Any]] | None,
        query: DiscoveryQuery,
        subject: str,
        path: str,
        params: dict[str, Any],
        search_term: str | None,
        mapper: Callable[[dict[str, Any]], dict[str, Any] | None],
        store_rows: Callable[[list[dict[str, Any]]], None],
    ) -> dict[str, Any]:
        if cached_rows is not None:
            return self._slice_discovery_rows(rows=cached_rows, query=query, subject=subject)

        pattern = query.compile_grep()
        current_search_term = search_term

        while True:
            request_params = dict(params)
            if current_search_term:
                request_params["search"] = current_search_term
            else:
                request_params.pop("search_namespaces", None)

            matched_rows: list[dict[str, Any]] = []
            all_rows: list[dict[str, Any]] | None = [] if current_search_term is None else None
            page = 1
            per_page = 100

            try:
                while True:
                    page_items, total_pages = self._get_paginated_page(
                        path,
                        params=request_params,
                        page=page,
                        per_page=per_page,
                    )
                    if not page_items:
                        break

                    for item in page_items:
                        row = mapper(item)
                        if row is None:
                            continue
                        if all_rows is not None:
                            all_rows.append(row)
                        if GitLabCodeMixin._matches_discovery_name(row, pattern):
                            matched_rows.append(row)
                            if len(matched_rows) >= query.required_matches:
                                window_end = query.skip + query.take
                                return build_discovery_payload(
                                    rows=matched_rows[query.skip:window_end],
                                    query=query,
                                    has_more=True,
                                    subject=subject,
                                )

                    if total_pages is not None:
                        if page >= total_pages:
                            break
                    elif len(page_items) < per_page:
                        break
                    page += 1
            except SmithApiError:
                if current_search_term is None:
                    raise
                current_search_term = None
                continue

            if all_rows is not None:
                store_rows(all_rows)
                return self._slice_discovery_rows(rows=all_rows, query=query, subject=subject)

            window_end = query.skip + query.take
            return build_discovery_payload(
                rows=matched_rows[query.skip:window_end],
                query=query,
                has_more=False,
                subject=subject,
            )

    def _load_group_rows(
        self: _GitLabDiscoveryProvider,
        *,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        if search is None and self._group_list_cache is not None:
            return self._group_list_cache

        params: dict[str, Any] = {"all_available": "false", "order_by": "path"}
        if search:
            params["search"] = search

        groups = self._get_paginated_list("/groups", params=params)
        mapped = [row for item in groups if (row := self._map_group_entry(item)) is not None]

        if search is None:
            self._group_list_cache = mapped
        return mapped

    def discover_groups(self: _GitLabDiscoveryProvider, *, query: DiscoveryQuery) -> dict[str, Any]:
        return self._discover_paginated_rows(
            cached_rows=self._group_list_cache,
            query=query,
            subject="groups",
            path="/groups",
            params={"all_available": "false", "order_by": "path"},
            search_term=None,
            mapper=self._map_group_entry,
            store_rows=lambda rows: setattr(self, "_group_list_cache", rows),
        )

    def list_groups(self: _GitLabDiscoveryProvider) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self._load_group_rows()]

    def _load_repository_rows(
        self: _GitLabDiscoveryProvider,
        *,
        group: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_group = str(group or "").strip().strip("/")
        cache_key = normalized_group.lower()
        if search is None:
            cached_entries = self._repository_list_cache.get(cache_key)
            if cached_entries is not None:
                return cached_entries

        if normalized_group:
            path = f"/groups/{quote(normalized_group, safe='')}/projects"
            params = {"include_subgroups": "true", "simple": "true", "order_by": "path"}
        else:
            path = "/projects"
            params = {"membership": "true", "simple": "true", "order_by": "path"}

        if search:
            params["search"] = search

        repos = self._get_paginated_list(path, params=params)
        mapped = [row for item in repos if (row := self._map_repository_entry(item)) is not None]

        if search is None:
            self._repository_list_cache[cache_key] = mapped
        return mapped

    def discover_repositories(self: _GitLabDiscoveryProvider, *, group: str | None = None, query: DiscoveryQuery) -> dict[str, Any]:
        normalized_group = str(group or "").strip().strip("/")
        cached_rows = self._repository_list_cache.get(normalized_group.lower())
        search_term = query.server_search_term() if not normalized_group else None
        if search_term and "/" in search_term:
            search_term = None
        if normalized_group:
            path = f"/groups/{quote(normalized_group, safe='')}/projects"
            params: dict[str, Any] = {"include_subgroups": "true", "simple": "true", "order_by": "path"}
        else:
            path = "/projects"
            params = {"membership": "true", "simple": "true", "order_by": "path"}
            if search_term:
                params["search_namespaces"] = "true"
        return self._discover_paginated_rows(
            cached_rows=cached_rows,
            query=query,
            subject="repositories",
            path=path,
            params=params,
            search_term=search_term,
            mapper=self._map_repository_entry,
            store_rows=lambda rows: self._repository_list_cache.__setitem__(normalized_group.lower(), rows),
        )

    def list_repositories(self: _GitLabDiscoveryProvider, *, group: str | None = None) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self._load_repository_rows(group=group)]

    def _search_result_project_path(self: Any, item: dict[str, Any], *, repo: str | None) -> str:
        if repo:
            return self._full_project_path(repo)

        for key in ("project_path_with_namespace", "path_with_namespace"):
            value = str(item.get(key) or "").strip().strip("/")
            if value:
                self._cache_project(
                    project_id=str(item.get("project_id") or "") or None,
                    full_path=value,
                )
                return value

        project_id = item.get("project_id")
        if project_id is not None:
            project_path = self._project_path_from_id(project_id)
            if project_path:
                return project_path

        fallback = str(item.get("project_path") or "").strip().strip("/")
        if fallback:
            return self._full_project_path(fallback)

        return self._full_project_path(repo) if repo else ""

    @staticmethod
    def _pagination_header_int(
        headers: Any,
        name: str,
        *,
        allow_zero: bool = False,
    ) -> int | None:
        if headers is None:
            return None

        raw_value = ""
        getter = getattr(headers, "get", None)
        if callable(getter):
            raw_value = str(getter(name, "") or "").strip()
        if not raw_value:
            return None

        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return None

        if value > 0 or (allow_zero and value == 0):
            return value
        return None

    def _search_code_page(
        self: Any,
        *,
        query: str,
        repo: str | None,
        page: int,
        per_page: int,
    ) -> tuple[list[dict[str, Any]], int | None, int | None]:
        if repo:
            path = f"/projects/{self._project_id(repo)}/search"
        else:
            group_getter = getattr(self, "_configured_gitlab_group", None)
            group = group_getter() if callable(group_getter) else None
            path = f"/groups/{quote(group, safe='')}/search" if group else "/search"

        response = self._request_response(
            "GET",
            path,
            params={"scope": "blobs", "search": query, "per_page": per_page, "page": page},
        )
        total_count = self._pagination_header_int(response.headers, "X-Total", allow_zero=True)
        next_page = self._pagination_header_int(response.headers, "X-Next-Page")

        if response.status_code == 204:
            return [], total_count, next_page

        body = response.text or ""
        if not body.strip():
            return [], total_count, next_page

        try:
            data = response.json()
        except ValueError as exc:
            raise SmithApiError(
                f"Expected JSON response from {self._build_url(path)} but received invalid JSON"
            ) from exc

        if not isinstance(data, list):
            return [], total_count, next_page

        page_items = [{**entry, "_repo_hint": repo} for entry in data if isinstance(entry, dict)]
        return page_items, total_count, next_page

    def search_code(
        self: Any,
        *,
        query: str,
        project: str | None = None,
        repos: list[str] | None = None,
        skip: int = 0,
        take: int = 20,
    ) -> dict[str, Any]:
        del project

        effective_repos = [item for item in (repos or []) if item]
        search_targets: list[str | None] = list(effective_repos) if effective_repos else [None]
        start = max(0, skip)
        window_size = max(1, take)
        stop = start + window_size
        per_page = 100
        total_matches = 0
        page_items_for_output: list[dict[str, Any]] = []
        warnings: list[str] = []
        partial = False
        matches_count_lower_bound = False

        for target_repo in search_targets:
            current_page = 1
            first_page_items, target_total_header, next_page = self._search_code_page(
                query=query,
                repo=target_repo,
                page=current_page,
                per_page=per_page,
            )

            if target_total_header is not None:
                target_total = max(target_total_header, len(first_page_items))
                local_start = min(max(start - total_matches, 0), target_total)
                local_stop = min(max(stop - total_matches, 0), target_total)

                target_items = list(first_page_items)
                while len(target_items) < local_stop and len(target_items) < target_total:
                    next_page_num = next_page if next_page is not None else current_page + 1
                    next_items, _ignored_total, next_page = self._search_code_page(
                        query=query,
                        repo=target_repo,
                        page=next_page_num,
                        per_page=per_page,
                    )
                    current_page = next_page_num
                    if not next_items:
                        break
                    target_items.extend(next_items)

                if local_start < local_stop:
                    page_items_for_output.extend(target_items[local_start:local_stop])
                total_matches += target_total
                continue

            target_seen = 0
            current_items = list(first_page_items)
            broad_search_without_total = target_repo is None
            checked_extra_page_after_window = False
            while True:
                for item in current_items:
                    global_index = total_matches + target_seen
                    if start <= global_index < stop:
                        page_items_for_output.append(item)
                    target_seen += 1

                maybe_more = next_page is not None or len(current_items) >= per_page
                if broad_search_without_total and target_seen >= stop:
                    if checked_extra_page_after_window or not maybe_more:
                        if checked_extra_page_after_window and maybe_more:
                            partial = True
                            matches_count_lower_bound = True
                            warning = (
                                "GitLab search did not provide an exact total; `matchesCount` is a lower bound. "
                                "Narrow with `--repo group/project` for exact counts."
                            )
                            if warning not in warnings:
                                warnings.append(warning)
                        break
                    checked_extra_page_after_window = True

                next_page_num = next_page if next_page is not None else current_page + 1
                next_items, _ignored_total, next_page = self._search_code_page(
                    query=query,
                    repo=target_repo,
                    page=next_page_num,
                    per_page=per_page,
                )
                current_page = next_page_num
                if not next_items:
                    break
                current_items = next_items

            total_matches += target_seen

        results: list[str] = []
        for item in page_items_for_output:
            project_path = self._search_result_project_path(
                item,
                repo=str(item.get("_repo_hint") or "") or None,
            )
            path = normalize_path(str(item.get("path") or item.get("filename") or ""))
            results.append(f"{project_path}:{path}" if project_path else path)

        result = {
            "matchesCount": total_matches,
            "results": results,
        }
        if warnings:
            result["warnings"] = warnings
        if partial:
            result["partial"] = True
        if matches_count_lower_bound:
            result["matchesCountLowerBound"] = True
        return result

    def _get_file_metadata(
        self: Any,
        *,
        repo: str,
        file_path: str,
        branch: str | None,
    ) -> dict[str, Any]:
        ref = normalize_branch_name(branch) or self._get_project_default_branch(repo)
        encoded_path = quote(file_path.lstrip("/"), safe="")
        return self._request_json(
            "GET",
            f"/projects/{self._project_id(repo)}/repository/files/{encoded_path}",
            params={"ref": ref},
        )

    def _get_repository_files(
        self: Any,
        *,
        repo: str,
        path: str | None,
        branch: str | None,
    ) -> list[dict[str, Any]]:
        normalized_path = normalize_path(path)
        prefix = normalized_path.strip("/")
        ref = normalize_branch_name(branch) or self._get_project_default_branch(repo)
        page = 1
        per_page = 100
        output: list[dict[str, Any]] = []

        while True:
            params: dict[str, Any] = {"ref": ref, "recursive": "true", "per_page": per_page, "page": page}
            if prefix:
                params["path"] = prefix
            try:
                data = self._request(
                    "GET",
                    f"/projects/{self._project_id(repo)}/repository/tree",
                    params=params,
                    expect_json=True,
                )
            except SmithApiError as exc:
                if prefix and exc.status_code == 404:
                    break
                raise
            if not isinstance(data, list):
                break
            page_items = [item for item in data if isinstance(item, dict)]
            if not page_items:
                break
            for item in page_items:
                if item.get("type") != "blob":
                    continue
                entry_path = str(item.get("path") or "").strip()
                if not entry_path:
                    continue
                output.append(
                    {
                        "path": f"/{entry_path.lstrip('/')}",
                        "is_binary": False,
                        "sha": str(item.get("id") or "") or None,
                    }
                )
            if len(page_items) < per_page:
                break
            page += 1

        if output or not prefix:
            return output

        try:
            metadata = self._get_file_metadata(repo=repo, file_path=normalized_path, branch=ref)
        except Exception:
            return []

        resolved_path = normalize_path(str(metadata.get("file_path") or prefix))
        return [
            {
                "path": resolved_path,
                "is_binary": False,
                "sha": str(metadata.get("blob_id") or "") or None,
            }
        ]

    def _get_file_text(
        self: Any,
        *,
        repo: str,
        file_path: str,
        branch: str | None,
        blob_sha: str | None = None,
        session: Any | None = None,
    ) -> str:
        if blob_sha:
            try:
                return self._request_text(
                    "GET",
                    f"/projects/{self._project_id(repo)}/repository/blobs/{quote(blob_sha, safe='')}/raw",
                    session=session,
                )
            except Exception:
                pass

        ref = normalize_branch_name(branch) or self._get_project_default_branch(repo)
        encoded_path = quote(file_path.lstrip("/"), safe="")
        return self._request_text(
            "GET",
            f"/projects/{self._project_id(repo)}/repository/files/{encoded_path}/raw",
            params={"ref": ref},
            session=session,
        )

    @staticmethod
    def _sanitize_cache_component(value: str) -> str:
        sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        return f"{sanitized or '_'}__{digest}"

    @staticmethod
    def _cache_lock(path: str) -> threading.Lock:
        with _CACHE_LOCKS_GUARD:
            lock = _CACHE_LOCKS.get(path)
            if lock is None:
                lock = threading.Lock()
                _CACHE_LOCKS[path] = lock
            return lock

    @staticmethod
    def _prepare_git_command(args: list[str], *, extra_configs: list[str] | None = None) -> list[str]:
        if not args or args[0] != "git":
            return args
        command = ["git", "-c", f"core.hooksPath={os.devnull}"]
        for config in extra_configs or []:
            command.extend(["-c", config])
        command.extend(args[1:])
        return command

    def _git_http_auth_extra_configs(self: Any) -> list[str]:
        try:
            token = self._get_token()
        except Exception as exc:
            logger.debug("GitLab token unavailable for local checkout auth, falling back to git credentials: %s", exc)
            return []

        encoded = base64.b64encode(f"oauth2:{token}".encode("utf-8")).decode("ascii")
        return [
            "credential.interactive=never",
            f"http.extraHeader=Authorization: Basic {encoded}",
        ]

    @staticmethod
    def _git_noninteractive_env() -> dict[str, str]:
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    def _git_subprocess(self: Any, args: list[str], *, cwd: str | None = None) -> None:
        subprocess.run(
            self._prepare_git_command(args),
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )

    def _git_auth_subprocess(self: Any, args: list[str], *, cwd: str | None = None) -> None:
        extra_configs = self._git_http_auth_extra_configs()
        subprocess.run(
            self._prepare_git_command(args, extra_configs=extra_configs),
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            env=self._git_noninteractive_env() if extra_configs else None,
        )

    def _git_auth_subprocess_output(self: Any, args: list[str], *, cwd: str | None = None) -> str:
        extra_configs = self._git_http_auth_extra_configs()
        result = subprocess.run(
            self._prepare_git_command(args, extra_configs=extra_configs),
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            env=self._git_noninteractive_env() if extra_configs else None,
        )
        return result.stdout

    def _git_subprocess_result(
        self: Any,
        args: list[str],
        *,
        cwd: str | None = None,
        check: bool = True,
    ) -> Any:
        return subprocess.run(
            self._prepare_git_command(args),
            cwd=cwd,
            check=check,
            capture_output=True,
            text=True,
        )

    def _git_subprocess_output(self: Any, args: list[str], *, cwd: str | None = None) -> str:
        result = subprocess.run(
            self._prepare_git_command(args),
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def _gitlab_grep_cache_root(self: Any) -> str:
        configured = (os.getenv("SMITH_GITLAB_GREP_CACHE_DIR") or "").strip()
        if configured:
            return configured
        return str(Path.home() / ".cache" / "smith" / "gitlab-grep")

    def _gitlab_grep_cache_max_age_seconds(self: Any) -> int:
        return parse_int_env(
            "GITLAB_GREP_CACHE_MAX_AGE_SECONDS",
            default=30,
            min_value=1,
            max_value=86_400,
        )

    def _local_checkout_path(self: Any, *, repo: str, branch: str) -> str:
        root = self._gitlab_grep_cache_root()
        host = self._gitlab_host()
        full_project_path = self._full_project_path(repo)
        path_segments = [
            self._sanitize_cache_component(host),
            *[
                self._sanitize_cache_component(segment)
                for segment in full_project_path.split("/")
                if segment.strip()
            ],
            self._sanitize_cache_component(branch),
        ]
        return os.path.join(root, *path_segments)

    def _local_checkout_remote_url(self: Any, *, repo: str) -> str:
        full_project_path = quote(self._full_project_path(repo), safe="/")
        return f"{self._gitlab_web_url()}/{full_project_path}.git"

    def _local_checkout_needs_refresh(self: Any, checkout_dir: str) -> bool:
        marker = self._local_checkout_refresh_marker(checkout_dir)
        if not os.path.isfile(marker):
            return True
        max_age = self._gitlab_grep_cache_max_age_seconds()
        age_seconds = time.time() - os.path.getmtime(marker)
        return age_seconds >= max_age

    def _mark_local_checkout_refreshed(self: Any, checkout_dir: str) -> None:
        marker = self._local_checkout_refresh_marker(checkout_dir)
        Path(marker).touch()

    @staticmethod
    def _local_checkout_refresh_marker(checkout_dir: str) -> str:
        return os.path.join(checkout_dir, ".git", "smith-last-fetch")

    def _reset_local_checkout(self: Any, checkout_dir: str) -> None:
        self._git_auth_subprocess(["git", "-C", checkout_dir, "reset", "--hard", "HEAD"])
        self._git_auth_subprocess(["git", "-C", checkout_dir, "clean", "-fd"])

    def _checkout_local_ref(self: Any, checkout_dir: str, ref: str) -> None:
        self._git_auth_subprocess(["git", "-C", checkout_dir, "checkout", "--force", "--detach", ref])

    def _local_checkout_has_expected_origin(self: Any, checkout_dir: str, remote_url: str) -> bool:
        try:
            origin_url = self._git_subprocess_output(["git", "-C", checkout_dir, "remote", "get-url", "origin"]).strip()
        except Exception:
            return False
        return origin_url == remote_url

    @staticmethod
    def _compute_sparse_patterns(path: str | None, glob: str | None) -> list[str] | None:
        return _local_checkout.compute_sparse_patterns(path, glob)

    def _apply_sparse_patterns(
        self: Any,
        checkout_dir: str,
        patterns: list[str] | None,
    ) -> None:
        _local_checkout.apply_sparse_patterns(self._git_auth_subprocess, checkout_dir, patterns)

    def _remote_head_sha(self: Any, checkout_dir: str, branch: str) -> str | None:
        return _local_checkout.remote_head_sha(self._git_auth_subprocess_output, checkout_dir, branch)

    def _local_head_sha(self: Any, checkout_dir: str) -> str | None:
        return _local_checkout.local_head_sha(self._git_subprocess_output, checkout_dir)

    def _ensure_local_checkout(
        self: Any,
        *,
        repo: str,
        branch: str,
        sparse_patterns: list[str] | None = None,
    ) -> str | None:
        if not parse_bool_env("GITLAB_GREP_USE_LOCAL_CACHE", default=True):
            return None

        checkout_dir = self._local_checkout_path(repo=repo, branch=branch)
        checkout_lock = self._cache_lock(checkout_dir)
        remote_url = self._local_checkout_remote_url(repo=repo)
        git_dir = os.path.join(checkout_dir, ".git")

        with checkout_lock:
            try:
                if not os.path.isdir(git_dir):
                    if os.path.exists(checkout_dir):
                        shutil.rmtree(checkout_dir)
                    os.makedirs(os.path.dirname(checkout_dir), exist_ok=True)

                    clone_args: list[str] = ["git", "clone", "--filter=blob:none"]
                    if sparse_patterns is not None:
                        clone_args.append("--sparse")
                    clone_args.extend(
                        [
                            "--depth",
                            "1",
                            "--branch",
                            branch,
                            "--single-branch",
                            remote_url,
                            checkout_dir,
                        ]
                    )

                    self._git_auth_subprocess(clone_args)
                    self._checkout_local_ref(checkout_dir, f"origin/{branch}")
                    self._reset_local_checkout(checkout_dir)
                    self._apply_sparse_patterns(checkout_dir, sparse_patterns)
                    self._mark_local_checkout_refreshed(checkout_dir)
                    return checkout_dir

                if not self._local_checkout_has_expected_origin(checkout_dir, remote_url):
                    logger.debug("Local checkout origin mismatch for %s, using API fallback", repo)
                    return None

                if self._local_checkout_needs_refresh(checkout_dir):
                    remote_sha = self._remote_head_sha(checkout_dir, branch)
                    local_sha = self._local_head_sha(checkout_dir)
                    if remote_sha and local_sha and remote_sha == local_sha:
                        self._apply_sparse_patterns(checkout_dir, sparse_patterns)
                        self._mark_local_checkout_refreshed(checkout_dir)
                        return checkout_dir

                    fetch_args: list[str] = [
                        "git",
                        "-C",
                        checkout_dir,
                        "fetch",
                        "--filter=blob:none",
                        "--depth",
                        "1",
                        "origin",
                        branch,
                    ]
                    self._git_auth_subprocess(fetch_args)
                    self._checkout_local_ref(checkout_dir, "FETCH_HEAD")
                    self._reset_local_checkout(checkout_dir)
                    self._apply_sparse_patterns(checkout_dir, sparse_patterns)
                    self._mark_local_checkout_refreshed(checkout_dir)
                    return checkout_dir

                self._apply_sparse_patterns(checkout_dir, sparse_patterns)
                return checkout_dir
            except Exception as exc:
                logger.debug("Local checkout unavailable for %s@%s, using API fallback: %s", repo, branch, exc)
                return None

    def _get_local_repository_files(
        self: Any,
        *,
        checkout_dir: str,
        path: str | None,
    ) -> list[dict[str, Any]]:
        checkout_root = os.path.realpath(checkout_dir)
        normalized_path = normalize_path(path)
        prefix = normalized_path.strip("/")

        if prefix:
            target = os.path.join(checkout_dir, prefix)
            if not self._is_path_within_checkout(target, checkout_root) or os.path.islink(target):
                return []
            if self._is_internal_local_path(prefix):
                return []
            if not os.path.exists(target):
                return []

        args = ["git", "-C", checkout_dir, "ls-files", "-z"]
        if prefix:
            args.extend(["--", prefix])
        raw_output = self._git_subprocess_output(args)

        output: list[dict[str, Any]] = []
        for item in raw_output.split("\0"):
            rel = item.strip().replace(os.sep, "/")
            if not rel or self._is_internal_local_path(rel):
                continue
            full_path = os.path.join(checkout_dir, rel.replace("/", os.sep))
            if os.path.islink(full_path) or not self._is_path_within_checkout(full_path, checkout_root):
                continue
            if not os.path.isfile(full_path):
                continue
            output.append(
                {
                    "path": f"/{rel.lstrip('/')}",
                    "is_binary": False,
                    "sha": None,
                    "local_path": full_path,
                }
            )
        return output

    @staticmethod
    def _read_local_file_text(local_path: str) -> str:
        with open(local_path, "rb") as file_handle:
            return file_handle.read().decode("utf-8", errors="replace")

    @staticmethod
    def _default_gitlab_grep_workers(candidate_files: int) -> int:
        if candidate_files <= 1:
            return 1
        if candidate_files <= 8:
            return 4
        if candidate_files <= 32:
            return 8
        return 8

    @staticmethod
    def _is_path_within_checkout(path: str, checkout_root: str) -> bool:
        return _local_checkout.is_path_within_checkout(path, checkout_root)

    @staticmethod
    def _is_internal_local_path(path: str) -> bool:
        return _local_checkout.is_internal_local_path(path)

    def _ripgrep_local_result(
        self: Any,
        *,
        checkout_dir: str,
        pattern: str,
        case_insensitive: bool,
        path: str | None,
        glob: str | None,
        filename_filter: re.Pattern[str],
        output_mode: Literal["content", "files_with_matches", "count"],
        context_lines: int,
        reverse: bool = False,
    ) -> dict[str, Any]:
        return _local_checkout.ripgrep_local_result(
            checkout_dir=checkout_dir,
            pattern=pattern,
            case_insensitive=case_insensitive,
            path=path,
            glob=glob,
            filename_filter=filename_filter,
            output_mode=output_mode,
            context_lines=context_lines,
            reverse=reverse,
            max_output_chars=self.max_output_chars,
            grep_max_files=self._config.grep_max_files,
            truncation_hint="Use from_line/to_line to read specific ranges, or narrow with path/glob/pattern.",
        )

    @staticmethod
    def _supports_search_api_literal_grep(
        pattern: str,
        *,
        case_insensitive: bool,
        from_line: int | None,
        to_line: int | None,
        reverse: bool = False,
    ) -> bool:
        if not case_insensitive or from_line is not None or to_line is not None:
            return False
        if reverse:
            return False
        return bool(re.fullmatch(r"[A-Za-z0-9 _/\-]+", pattern))

    @staticmethod
    def _path_is_within_scope(file_path: str, scoped_path: str) -> bool:
        if scoped_path == "/":
            return True
        normalized_file_path = normalize_path(file_path)
        normalized_scope = normalize_path(scoped_path)
        scope_prefix = normalized_scope.rstrip("/")
        return normalized_file_path == normalized_scope or normalized_file_path.startswith(f"{scope_prefix}/")

    def _grep_via_search_api(
        self: Any,
        *,
        repo: str,
        pattern: str,
        path: str,
        branch: str | None,
        filename_filter: re.Pattern[str],
        case_insensitive: bool,
        from_line: int | None,
        to_line: int | None,
        reverse: bool = False,
    ) -> list[dict[str, Any]] | None:
        if not self._supports_search_api_literal_grep(
            pattern,
            case_insensitive=case_insensitive,
            from_line=from_line,
            to_line=to_line,
            reverse=reverse,
        ):
            return None

        page = 1
        per_page = 100
        matched_files: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        params_base: dict[str, Any] = {"scope": "blobs", "search": pattern, "per_page": per_page}
        if branch:
            params_base["ref"] = branch

        try:
            while True:
                data = self._request(
                    "GET",
                    f"/projects/{self._project_id(repo)}/search",
                    params={**params_base, "page": page},
                    expect_json=True,
                )
                if not isinstance(data, list):
                    break
                page_items = [item for item in data if isinstance(item, dict)]
                if not page_items:
                    break
                for item in page_items:
                    file_path = normalize_path(str(item.get("path") or item.get("filename") or ""))
                    if not file_path or file_path == "/":
                        continue
                    if not self._path_is_within_scope(file_path, path):
                        continue
                    if not filename_filter.search(os.path.basename(file_path)):
                        continue
                    if file_path in seen_paths:
                        continue
                    seen_paths.add(file_path)
                    matched_files.append(
                        {
                            "path": file_path,
                            "is_binary": False,
                            "sha": None,
                            "local_path": None,
                        }
                    )
                if len(page_items) < per_page:
                    break
                page += 1
        except SmithApiError:
            return None

        return matched_files

    def grep(
        self: Any,
        *,
        repo: str,
        pattern: str | None = None,
        path: str | None = None,
        branch: str | None = None,
        glob: str | None = None,
        output_mode: Literal["content", "files_with_matches", "count"] = "content",
        case_insensitive: bool = True,
        context_lines: int | None = 3,
        from_line: int | None = None,
        to_line: int | None = None,
        reverse: bool = False,
        no_clone: bool = False,
    ) -> dict[str, Any]:
        folder_path = normalize_path(path)
        normalized_branch = normalize_branch_name(branch)
        regex_pattern = pattern or ".*"
        is_match_all = match_all_pattern(regex_pattern)

        file_regex = glob_to_regex(glob) if glob else ".*"
        filename_filter = re.compile(file_regex)

        resolved_branch = normalized_branch or self._get_project_default_branch(repo)
        grep_local_cache_enabled = parse_bool_env("GITLAB_GREP_USE_LOCAL_CACHE", default=True)
        use_local_cache = grep_local_cache_enabled and not no_clone
        sparse_patterns = self._compute_sparse_patterns(path, glob) if use_local_cache else None
        checkout_dir = (
            self._ensure_local_checkout(
                repo=repo,
                branch=resolved_branch,
                sparse_patterns=sparse_patterns,
            )
            if use_local_cache
            else None
        )
        search_pattern: re.Pattern[str] | None = None

        if checkout_dir and not is_match_all and from_line is None and to_line is None:
            search_pattern, compile_error = compile_search_pattern(
                regex_pattern,
                case_insensitive=case_insensitive,
            )
            if compile_error or search_pattern is None:
                return grep_compile_error_result(compile_error or "Invalid pattern")

            return self._ripgrep_local_result(
                checkout_dir=checkout_dir,
                pattern=regex_pattern,
                case_insensitive=case_insensitive,
                path=path,
                glob=glob,
                filename_filter=filename_filter,
                output_mode=output_mode,
                context_lines=context_lines or 0,
                reverse=reverse,
            )

        matching = None

        search_api_candidates: list[dict[str, Any]] | None = None
        if matching is None and not is_match_all and checkout_dir is None and use_local_cache:
            search_api_candidates = self._grep_via_search_api(
                repo=repo,
                pattern=regex_pattern,
                path=folder_path,
                branch=normalized_branch,
                filename_filter=filename_filter,
                case_insensitive=case_insensitive,
                from_line=from_line,
                to_line=to_line,
                reverse=reverse,
            )

        if matching is None:
            if search_api_candidates is not None:
                files = search_api_candidates
            elif checkout_dir:
                files = self._get_local_repository_files(checkout_dir=checkout_dir, path=path)
            else:
                files = self._get_repository_files(
                    repo=repo,
                    path=folder_path,
                    branch=resolved_branch,
                )

            matching = [
                {
                    "path": file_item.get("path", ""),
                    "is_binary": bool(file_item.get("is_binary")),
                    "sha": file_item.get("sha"),
                    "local_path": file_item.get("local_path"),
                }
                for file_item in files
                if filename_filter.search(os.path.basename(str(file_item.get("path", ""))))
            ]
            if len(matching) > self._config.grep_max_files:
                return grep_too_many_files_result(len(matching), self._config.grep_max_files)

        if output_mode == "files_with_matches" and is_match_all:
            ordered_matching = list(reversed(matching)) if reverse else matching
            text = "\n".join(str(item.get("path", "")) for item in ordered_matching)
            text = truncate_output(
                text,
                self.max_output_chars,
                "Narrow results with a more specific path or glob filter.",
            )
            return {
                "text": text,
                "files_matched": len(ordered_matching),
                "warnings": [],
                "partial": False,
            }

        if search_pattern is None:
            search_pattern, compile_error = compile_search_pattern(
                regex_pattern,
                case_insensitive=case_insensitive,
            )
            if compile_error or search_pattern is None:
                return grep_compile_error_result(compile_error or "Invalid pattern")

        output_lines: list[str] = []
        warnings: list[str] = []
        files_matched = 0
        grep_parallel_enabled = parse_bool_env("GITLAB_GREP_ENABLE_PARALLEL", default=True)
        grep_max_workers = parse_int_env(
            "GITLAB_GREP_MAX_WORKERS",
            default=self._default_gitlab_grep_workers(len(matching)),
            min_value=1,
            max_value=8,
        )

        def _process_file(
            file_path: str,
            blob_sha: str | None,
            local_path: str | None,
            *,
            session: Any | None = None,
        ) -> tuple[list[str], int, str | None]:
            try:
                if local_path:
                    content = self._read_local_file_text(local_path)
                else:
                    content = self._get_file_text(
                        repo=repo,
                        file_path=file_path,
                        branch=resolved_branch,
                        blob_sha=blob_sha,
                        session=session,
                    )
            except Exception as exc:
                return [], 0, f"failed to read {file_path}: {exc}"

            all_lines = content.splitlines()
            lines = slice_lines(
                all_lines,
                from_line=from_line,
                to_line=to_line,
            )
            line_offset = (from_line - 1) if from_line and from_line > 0 else 0

            matched_lines, count = grep_match_lines(
                lines=lines,
                search_pattern=search_pattern,
                file_label=file_path,
                output_mode=output_mode,
                context_lines=context_lines or 0,
                line_offset=line_offset,
                reverse=reverse,
            )
            if not count:
                return [], 0, None

            return matched_lines, count, None

        def _process_file_in_worker(
            file_path: str,
            blob_sha: str | None,
            local_path: str | None,
        ) -> tuple[list[str], int, str | None]:
            return _process_file(
                file_path,
                blob_sha,
                local_path,
                session=self._get_http_session(),
            )

        file_entries = [
            (
                str(item.get("path", "")),
                str(item.get("sha") or "") or None,
                str(item.get("local_path") or "") or None,
            )
            for item in matching
            if str(item.get("path", "")) and not bool(item.get("is_binary"))
        ]
        if reverse:
            file_entries.reverse()
        effective_workers = min(grep_max_workers, len(file_entries) or 1)
        use_parallel = not checkout_dir and grep_parallel_enabled and effective_workers > 1 and len(file_entries) > 1

        if use_parallel:
            with ThreadPoolExecutor(max_workers=max(1, effective_workers)) as executor:
                futures = [
                    executor.submit(_process_file_in_worker, file_path, blob_sha, local_path)
                    for file_path, blob_sha, local_path in file_entries
                ]
                for (file_path, _blob_sha, _local_path), future in zip(file_entries, futures):
                    try:
                        lines_out, matched_count, warning = future.result()
                    except Exception as exc:
                        warnings.append(f"failed to read {file_path}: {exc}")
                        continue
                    if warning:
                        warnings.append(warning)
                        continue
                    files_matched += matched_count
                    output_lines.extend(lines_out)
        else:
            for file_path, blob_sha, local_path in file_entries:
                lines_out, matched_count, warning = _process_file(file_path, blob_sha, local_path)
                if warning:
                    warnings.append(warning)
                    continue
                files_matched += matched_count
                output_lines.extend(lines_out)

        return build_grep_result(
            output_lines=output_lines,
            matched_count=files_matched,
            warnings=warnings,
            max_output_chars=self.max_output_chars,
            truncation_hint="Use from_line/to_line to read specific ranges, or narrow with path/glob/pattern.",
        )
