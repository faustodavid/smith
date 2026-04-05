from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote

from smith.config import parse_bool_env, parse_int_env
from smith.errors import SmithApiError
from smith.formatting import glob_to_regex, normalize_branch_name, truncate_output
from smith.providers.helpers import build_grep_result, grep_compile_error_result, grep_match_lines
from smith.utils import compile_search_pattern, match_all_pattern, normalize_path, slice_lines

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_CACHE_LOCKS: dict[str, threading.Lock] = {}
_CACHE_LOCKS_GUARD = threading.Lock()


class GitLabCodeMixin:
    _repository_list_cache: list[dict[str, Any]] | None

    def list_projects(self: Any) -> list[dict[str, Any]]:
        group = self._require_gitlab_group()
        return [
            {
                "id": group,
                "name": group,
                "state": "active",
                "url": f"{self._gitlab_web_url()}/{group}",
            }
        ]

    def list_repositories(self: Any) -> list[dict[str, Any]]:
        cache = getattr(self, "_repository_list_cache", None)
        if cache is not None:
            return [dict(entry) for entry in cache]

        group = self._require_gitlab_group()
        repos = self._get_paginated_list(
            f"/groups/{quote(group, safe='')}/projects",
            params={"include_subgroups": "true", "simple": "true", "order_by": "path"},
        )
        mapped: list[dict[str, Any]] = []
        for item in repos:
            if not isinstance(item, dict):
                continue
            full_path = str(item.get("path_with_namespace") or "").strip().strip("/")
            if not full_path:
                continue
            relative = self._relative_repo_path(full_path)
            self._cache_project(
                project_id=str(item.get("id") or "") or None,
                full_path=full_path,
                relative_path=relative,
                default_branch=str(item.get("default_branch") or "") or None,
            )
            mapped.append(
                {
                    "id": item.get("id"),
                    "name": relative,
                    "defaultBranch": item.get("default_branch"),
                    "webUrl": item.get("web_url"),
                }
            )

        self._repository_list_cache = mapped
        return [dict(entry) for entry in mapped]

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

        return self._full_project_path(repo) if repo else self._require_gitlab_group()

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

        group = self._require_gitlab_group()
        effective_repos = [item for item in (repos or []) if item]
        search_targets: list[str | None] = list(effective_repos) if effective_repos else [None]
        desired = max(1, skip + take)
        all_items: list[dict[str, Any]] = []

        if not effective_repos:
            self.list_repositories()

        for target_repo in search_targets:
            page = 1
            while len(all_items) < desired:
                remaining = max(1, desired - len(all_items))
                per_page = min(100, remaining)
                if target_repo:
                    path = f"/projects/{self._project_id(target_repo)}/search"
                else:
                    path = f"/groups/{quote(group, safe='')}/search"
                data = self._request(
                    "GET",
                    path,
                    params={"scope": "blobs", "search": query, "per_page": per_page, "page": page},
                    expect_json=True,
                )
                if not isinstance(data, list):
                    break
                page_items = [{**entry, "_repo_hint": target_repo} for entry in data if isinstance(entry, dict)]
                if not page_items:
                    break
                all_items.extend(page_items)
                if len(page_items) < per_page:
                    break
                page += 1

        sliced = all_items[max(0, skip) : max(0, skip) + max(1, take)]
        results: list[str] = []
        for item in sliced:
            project_path = self._search_result_project_path(
                item,
                repo=str(item.get("_repo_hint") or "") or None,
            )
            path = normalize_path(str(item.get("path") or item.get("filename") or ""))
            results.append(f"{project_path}:{path}")

        return {
            "matchesCount": max(len(all_items), max(0, skip) + len(results)),
            "results": results,
        }

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
    ) -> str:
        if blob_sha:
            try:
                return self._request_text(
                    "GET",
                    f"/projects/{self._project_id(repo)}/repository/blobs/{quote(blob_sha, safe='')}/raw",
                )
            except Exception:
                pass

        ref = normalize_branch_name(branch) or self._get_project_default_branch(repo)
        encoded_path = quote(file_path.lstrip("/"), safe="")
        return self._request_text(
            "GET",
            f"/projects/{self._project_id(repo)}/repository/files/{encoded_path}/raw",
            params={"ref": ref},
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
    def _prepare_git_command(args: list[str]) -> list[str]:
        if not args or args[0] != "git":
            return args
        return ["git", "-c", f"core.hooksPath={os.devnull}", *args[1:]]

    def _git_subprocess(self: Any, args: list[str], *, cwd: str | None = None) -> None:
        subprocess.run(
            self._prepare_git_command(args),
            cwd=cwd,
            check=True,
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
            default=300,
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
        self._git_subprocess(["git", "-C", checkout_dir, "reset", "--hard", "HEAD"])
        self._git_subprocess(["git", "-C", checkout_dir, "clean", "-fd"])

    def _checkout_local_ref(self: Any, checkout_dir: str, ref: str) -> None:
        self._git_subprocess(["git", "-C", checkout_dir, "checkout", "--force", "--detach", ref])

    def _local_checkout_has_expected_origin(self: Any, checkout_dir: str, remote_url: str) -> bool:
        try:
            origin_url = self._git_subprocess_output(["git", "-C", checkout_dir, "remote", "get-url", "origin"]).strip()
        except Exception:
            return False
        return origin_url == remote_url

    def _ensure_local_checkout(
        self: Any,
        *,
        repo: str,
        branch: str,
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
                    self._git_subprocess(
                        [
                            "git",
                            "clone",
                            "--depth",
                            "1",
                            "--branch",
                            branch,
                            "--single-branch",
                            remote_url,
                            checkout_dir,
                        ]
                    )
                    self._checkout_local_ref(checkout_dir, f"origin/{branch}")
                    self._reset_local_checkout(checkout_dir)
                    self._mark_local_checkout_refreshed(checkout_dir)
                    return checkout_dir

                if not self._local_checkout_has_expected_origin(checkout_dir, remote_url):
                    logger.debug("Local checkout origin mismatch for %s, using API fallback", repo)
                    return None

                if self._local_checkout_needs_refresh(checkout_dir):
                    self._git_subprocess(["git", "-C", checkout_dir, "fetch", "--depth", "1", "origin", branch])
                    self._checkout_local_ref(checkout_dir, "FETCH_HEAD")
                    self._reset_local_checkout(checkout_dir)
                    self._mark_local_checkout_refreshed(checkout_dir)
                    return checkout_dir

                self._checkout_local_ref(checkout_dir, f"origin/{branch}")
                self._reset_local_checkout(checkout_dir)
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
    def _is_path_within_checkout(path: str, checkout_root: str) -> bool:
        try:
            return os.path.commonpath([os.path.realpath(path), checkout_root]) == checkout_root
        except ValueError:
            return False

    @staticmethod
    def _is_internal_local_path(path: str) -> bool:
        normalized = path.strip().lstrip("/").replace("\\", "/")
        return normalized == ".git" or normalized.startswith(".git/")

    @staticmethod
    def _supports_search_api_literal_grep(
        pattern: str,
        *,
        case_insensitive: bool,
        from_line: int | None,
        to_line: int | None,
    ) -> bool:
        if not case_insensitive or from_line is not None or to_line is not None:
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
    ) -> list[dict[str, Any]] | None:
        if not self._supports_search_api_literal_grep(
            pattern,
            case_insensitive=case_insensitive,
            from_line=from_line,
            to_line=to_line,
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
    ) -> dict[str, Any]:
        folder_path = normalize_path(path)
        normalized_branch = normalize_branch_name(branch)
        regex_pattern = pattern or ".*"
        is_match_all = match_all_pattern(regex_pattern)

        file_regex = glob_to_regex(glob) if glob else ".*"
        filename_filter = re.compile(file_regex)

        search_api_candidates: list[dict[str, Any]] | None = None
        if output_mode == "files_with_matches" and not is_match_all:
            search_api_candidates = self._grep_via_search_api(
                repo=repo,
                pattern=regex_pattern,
                path=folder_path,
                branch=normalized_branch,
                filename_filter=filename_filter,
                case_insensitive=case_insensitive,
                from_line=from_line,
                to_line=to_line,
            )

        resolved_branch = normalized_branch or self._get_project_default_branch(repo)
        checkout_dir: str | None = None
        if search_api_candidates is not None:
            files = search_api_candidates
        else:
            checkout_dir = self._ensure_local_checkout(repo=repo, branch=resolved_branch)
            if checkout_dir:
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

        if output_mode == "files_with_matches" and is_match_all:
            text = "\n".join(str(item.get("path", "")) for item in matching)
            text = truncate_output(
                text,
                self.max_output_chars,
                "Narrow results with a more specific path or glob filter.",
            )
            return {
                "text": text,
                "files_matched": len(matching),
                "warnings": [],
                "partial": False,
            }

        search_pattern, compile_error = compile_search_pattern(
            regex_pattern,
            case_insensitive=case_insensitive,
        )
        if compile_error or search_pattern is None:
            return grep_compile_error_result(compile_error or "Invalid pattern")

        output_lines: list[str] = []
        warnings: list[str] = []
        files_matched = 0

        for file_item in matching:
            file_path = str(file_item.get("path") or "")
            if not file_path or file_item.get("is_binary"):
                continue

            try:
                local_path = str(file_item.get("local_path") or "") or None
                if local_path:
                    content = self._read_local_file_text(local_path)
                else:
                    content = self._get_file_text(
                        repo=repo,
                        file_path=file_path,
                        branch=resolved_branch,
                        blob_sha=str(file_item.get("sha") or "") or None,
                    )
            except Exception as exc:
                warnings.append(f"failed to read {file_path}: {exc}")
                continue

            lines = slice_lines(
                content.splitlines(),
                from_line=from_line,
                to_line=to_line,
            )

            matched_lines, count = grep_match_lines(
                lines=lines,
                search_pattern=search_pattern,
                file_label=file_path,
                output_mode=output_mode,
                context_lines=context_lines or 0,
            )
            if count:
                files_matched += count
                output_lines.extend(matched_lines)

        return build_grep_result(
            output_lines=output_lines,
            matched_count=files_matched,
            warnings=warnings,
            max_output_chars=self.max_output_chars,
            truncation_hint="Use from_line/to_line to read specific ranges, or narrow with path/glob/pattern.",
        )
