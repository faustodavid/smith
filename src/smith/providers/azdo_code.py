from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote

from smith.config import parse_bool_env, parse_int_env
from smith.formatting import glob_to_regex, normalize_branch_name, truncate_output
from smith.providers import local_checkout as _local_checkout
from smith.providers.helpers import (
    build_grep_result,
    grep_compile_error_result,
    grep_match_lines,
    grep_too_many_files_result,
)
from smith.utils import (
    compile_search_pattern,
    match_all_pattern,
    normalize_path,
    slice_lines,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_CACHE_LOCKS: dict[str, threading.Lock] = {}
_CACHE_LOCKS_GUARD = threading.Lock()


class AzdoCodeMixin:
    def list_projects(self: Any) -> list[dict[str, Any]]:
        url = f"{self.org_url}/_apis/projects"
        params = {"api-version": self.api_version}
        data = self._request_json("GET", url, params=params)
        values = data.get("value", [])
        return [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "state": item.get("state"),
                "url": item.get("url"),
            }
            for item in values
            if isinstance(item, dict)
        ]

    def list_repositories(self: Any, project: str) -> list[dict[str, Any]]:
        url = f"{self.org_url}/{project}/_apis/git/repositories"
        params = {"api-version": self.api_version}
        data = self._request_json("GET", url, params=params)
        values = data.get("value", [])
        return [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "defaultBranch": item.get("defaultBranch"),
                "webUrl": item.get("webUrl"),
            }
            for item in values
            if isinstance(item, dict)
        ]

    def search_code(
        self: Any,
        *,
        query: str,
        project: str | None = None,
        repos: list[str] | None = None,
        skip: int = 0,
        take: int = 20,
    ) -> dict[str, Any]:
        if repos and not project:
            raise ValueError("Repository filter requires --project")

        filters: dict[str, Any] = {}
        if project:
            filters["Project"] = [project]
        if repos:
            filters["Repository"] = repos

        payload: dict[str, Any] = {
            "searchText": query,
            "$skip": max(0, skip),
            "$top": max(1, take),
            "filters": filters,
            "$orderBy": [{"field": "filename", "sortOrder": "ASC"}],
        }

        url = self._almsearch_url(f"/_apis/search/codesearchresults?api-version={self.api_version}")
        data = self._request_json(
            "POST",
            url,
            json_body=payload,
            headers={"Content-Type": "application/json"},
        )

        results = []
        for item in data.get("results", []):
            if not isinstance(item, dict):
                continue
            project_name = (item.get("project") or {}).get("name", "")
            repo_name = (item.get("repository") or {}).get("name", "")
            path = item.get("path", "")
            results.append(f"{project_name}/{repo_name}:{path}")

        return {
            "matchesCount": data.get("count", len(results)),
            "results": results,
        }

    def _get_repository_default_branch(self: Any, project: str, repo: str) -> str:
        cache: dict[str, str] = getattr(self, "_azdo_default_branch_cache", {})
        if not hasattr(self, "_azdo_default_branch_cache"):
            self._azdo_default_branch_cache = cache
        cache_key = f"{project.strip().lower()}/{repo.strip().lower()}"
        cached = cache.get(cache_key)
        if cached:
            return cached

        url = f"{self.org_url}/{project}/_apis/git/repositories/{repo}"
        params = {"api-version": self.api_version}
        try:
            data = self._request_json("GET", url, params=params)
        except Exception:
            return "main"
        branch = str(data.get("defaultBranch") or "").strip()
        if branch.startswith("refs/heads/"):
            branch = branch[len("refs/heads/") :]
        resolved = branch or "main"
        cache[cache_key] = resolved
        return resolved

    def _get_repository_files(
        self: Any,
        *,
        project: str,
        repo: str,
        path: str,
        branch: str | None,
    ) -> list[dict[str, Any]]:
        url = f"{self.org_url}/{project}/_apis/git/repositories/{repo}/items"
        params: dict[str, Any] = {
            "scopePath": path,
            "recursionLevel": "Full",
            "includeContentMetadata": "true",
            "api-version": self.api_version,
        }

        if branch:
            params["versionDescriptor.version"] = branch
            params["versionDescriptor.versionType"] = "branch"

        data = self._request_json("GET", url, params=params)
        values = data.get("value", [])
        files: list[dict[str, Any]] = []
        for item in values:
            if not isinstance(item, dict):
                continue
            if item.get("gitObjectType") != "blob":
                continue
            files.append(item)
        return files

    def _get_file_text(
        self: Any,
        *,
        project: str,
        repo: str,
        file_path: str,
        branch: str | None,
    ) -> str:
        url = f"{self.org_url}/{project}/_apis/git/repositories/{repo}/items"
        params: dict[str, Any] = {
            "path": file_path,
            "includeContent": "true",
            "api-version": self.api_version,
        }
        if branch:
            params["versionDescriptor.version"] = branch
            params["versionDescriptor.versionType"] = "branch"

        data = self._request_json("GET", url, params=params)
        content = data.get("content")
        if isinstance(content, str):
            return content

        params["$format"] = "text"
        return self._request_text("GET", url, params=params)

    @staticmethod
    def _sanitize_cache_component(value: str) -> str:
        sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
        return sanitized or "_"

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
            logger.debug(
                "Azure DevOps token unavailable for local checkout auth: %s",
                exc,
            )
            return []

        return [
            "credential.interactive=never",
            f"http.extraHeader=Authorization: Bearer {token}",
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

    def _azdo_grep_cache_root(self: Any) -> str:
        configured = (os.getenv("SMITH_AZDO_GREP_CACHE_DIR") or "").strip()
        if configured:
            return configured
        return str(Path.home() / ".cache" / "smith" / "azdo-grep")

    def _azdo_grep_cache_max_age_seconds(self: Any) -> int:
        return parse_int_env(
            "AZDO_GREP_CACHE_MAX_AGE_SECONDS",
            default=30,
            min_value=1,
            max_value=86_400,
        )

    def _local_checkout_path(self: Any, *, project: str, repo: str, branch: str) -> str:
        root = self._azdo_grep_cache_root()
        org = str(getattr(self, "org_name", None) or "azdo")
        return os.path.join(
            root,
            self._sanitize_cache_component(org),
            self._sanitize_cache_component(project),
            self._sanitize_cache_component(repo),
            self._sanitize_cache_component(branch),
        )

    def _local_checkout_remote_url(self: Any, *, project: str, repo: str) -> str:
        encoded_project = quote(project, safe="")
        encoded_repo = quote(repo, safe="")
        return f"{self.org_url}/{encoded_project}/_git/{encoded_repo}"

    def _local_checkout_needs_refresh(self: Any, checkout_dir: str) -> bool:
        marker = self._local_checkout_refresh_marker(checkout_dir)
        if not os.path.isfile(marker):
            return True
        max_age = self._azdo_grep_cache_max_age_seconds()
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
        self._git_subprocess(
            ["git", "-C", checkout_dir, "checkout", "--force", "--detach", ref]
        )

    def _local_checkout_has_expected_origin(self: Any, checkout_dir: str, remote_url: str) -> bool:
        try:
            origin_url = self._git_subprocess_output(
                ["git", "-C", checkout_dir, "remote", "get-url", "origin"]
            ).strip()
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
        _local_checkout.apply_sparse_patterns(self._git_subprocess, checkout_dir, patterns)

    def _remote_head_sha(self: Any, checkout_dir: str, branch: str) -> str | None:
        return _local_checkout.remote_head_sha(self._git_subprocess_output, checkout_dir, branch)

    def _local_head_sha(self: Any, checkout_dir: str) -> str | None:
        return _local_checkout.local_head_sha(self._git_subprocess_output, checkout_dir)

    def _ensure_local_checkout(
        self: Any,
        *,
        project: str,
        repo: str,
        branch: str,
        sparse_patterns: list[str] | None = None,
    ) -> str | None:
        if not parse_bool_env("AZDO_GREP_USE_LOCAL_CACHE", default=True):
            return None

        checkout_dir = self._local_checkout_path(project=project, repo=repo, branch=branch)
        checkout_lock = self._cache_lock(checkout_dir)
        remote_url = self._local_checkout_remote_url(project=project, repo=repo)
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
                    logger.debug(
                        "Local checkout origin mismatch for %s/%s, using API fallback",
                        project,
                        repo,
                    )
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
                logger.debug(
                    "Local checkout unavailable for %s/%s@%s, using API fallback: %s",
                    project,
                    repo,
                    branch,
                    exc,
                )
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
        try:
            raw_output = self._git_subprocess_output(args)
        except Exception:
            return []

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
        return _local_checkout.is_path_within_checkout(path, checkout_root)

    @staticmethod
    def _is_internal_local_path(path: str) -> bool:
        return _local_checkout.is_internal_local_path(path)

    @staticmethod
    def _default_azdo_grep_workers(candidate_files: int) -> int:
        if candidate_files <= 1:
            return 1
        if candidate_files <= 8:
            return 4
        if candidate_files <= 32:
            return 8
        return 8

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

    def grep(
        self: Any,
        *,
        project: str,
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

        grep_local_cache_enabled = parse_bool_env("AZDO_GREP_USE_LOCAL_CACHE", default=True)
        use_local_cache = grep_local_cache_enabled and not no_clone
        resolved_branch = normalized_branch or (
            self._get_repository_default_branch(project, repo) if use_local_cache else None
        )
        sparse_patterns = self._compute_sparse_patterns(path, glob) if use_local_cache else None
        checkout_dir: str | None = None
        if use_local_cache and resolved_branch:
            checkout_dir = self._ensure_local_checkout(
                project=project,
                repo=repo,
                branch=resolved_branch,
                sparse_patterns=sparse_patterns,
            )

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

        if checkout_dir:
            files = self._get_local_repository_files(checkout_dir=checkout_dir, path=path)
        else:
            files = self._get_repository_files(
                project=project,
                repo=repo,
                path=folder_path,
                branch=normalized_branch,
            )

        matching = [
            {
                "path": str(file_item.get("path", "")),
                "is_binary": bool(
                    file_item.get("isBinary")
                    or file_item.get("is_binary")
                    or (file_item.get("contentMetadata") or {}).get("isBinary")
                ),
                "local_path": file_item.get("local_path"),
            }
            for file_item in files
            if filename_filter.search(os.path.basename(str(file_item.get("path", ""))))
        ]
        if len(matching) > self._config.grep_max_files:
            return grep_too_many_files_result(len(matching), self._config.grep_max_files)

        if output_mode == "files_with_matches" and is_match_all:
            ordered_matching = list(reversed(matching)) if reverse else matching
            text = "\n".join(item["path"] for item in ordered_matching)
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

        search_pattern, compile_error = compile_search_pattern(
            regex_pattern,
            case_insensitive=case_insensitive,
        )
        if compile_error or search_pattern is None:
            return grep_compile_error_result(compile_error or "Invalid pattern")

        output_lines: list[str] = []
        warnings: list[str] = []
        files_matched = 0

        grep_parallel_enabled = parse_bool_env("AZDO_GREP_ENABLE_PARALLEL", default=True)
        grep_max_workers = parse_int_env(
            "AZDO_GREP_MAX_WORKERS",
            default=self._default_azdo_grep_workers(len(matching)),
            min_value=1,
            max_value=8,
        )

        def _process_file(
            file_path: str,
            local_path: str | None,
        ) -> tuple[list[str], int, str | None]:
            try:
                if local_path:
                    content = self._read_local_file_text(local_path)
                else:
                    content = self._get_file_text(
                        project=project,
                        repo=repo,
                        file_path=file_path,
                        branch=normalized_branch,
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

        file_entries = [
            (item["path"], item.get("local_path"))
            for item in matching
            if item["path"] and not item["is_binary"]
        ]
        if reverse:
            file_entries.reverse()

        effective_workers = min(grep_max_workers, len(file_entries) or 1)
        use_parallel = (
            not checkout_dir
            and grep_parallel_enabled
            and effective_workers > 1
            and len(file_entries) > 1
        )

        if use_parallel:
            with ThreadPoolExecutor(max_workers=max(1, effective_workers)) as executor:
                futures = [
                    executor.submit(_process_file, file_path, local_path)
                    for file_path, local_path in file_entries
                ]
                for (file_path, _local_path), future in zip(file_entries, futures):
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
            for file_path, local_path in file_entries:
                lines_out, matched_count, warning = _process_file(file_path, local_path)
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
