from __future__ import annotations

import base64
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

import requests

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


class GitHubCodeMixin:
    _repository_list_cache: list[dict[str, Any]] | None

    def list_projects(self: Any) -> list[dict[str, Any]]:
        org = self._require_github_org()
        return [
            {
                "id": org,
                "name": org,
                "state": "active",
                "url": f"https://github.com/{org}",
            }
        ]

    def list_repositories(self: Any) -> list[dict[str, Any]]:
        cache = getattr(self, "_repository_list_cache", None)
        if cache is not None:
            return [dict(entry) for entry in cache]

        org = self._require_github_org()
        repos = self._get_paginated_list(f"/orgs/{quote(org, safe='')}/repos")
        mapped = [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "defaultBranch": item.get("default_branch"),
                "webUrl": item.get("html_url"),
            }
            for item in repos
            if isinstance(item, dict)
        ]
        self._repository_list_cache = mapped
        return [dict(entry) for entry in mapped]

    def search_code(
        self: Any,
        *,
        query: str,
        project: str | None = None,
        repos: list[str] | None = None,
        skip: int = 0,
        take: int = 20,
    ) -> dict[str, Any]:
        org = self._require_github_org()
        effective_repos = [item for item in (repos or []) if item]

        search_targets: list[str | None] = list(effective_repos) if effective_repos else [None]
        desired = max(1, skip + take)
        all_items: list[dict[str, Any]] = []
        total_count = 0

        for target_repo in search_targets:
            page = 1
            while len(all_items) < desired:
                remaining = max(1, desired - len(all_items))
                per_page = min(100, remaining)
                qualifiers = [query]
                if target_repo:
                    qualifiers.append(f"repo:{org}/{target_repo}")
                else:
                    qualifiers.append(f"org:{org}")
                q = " ".join(part for part in qualifiers if part.strip())
                data = self._request_json(
                    "GET",
                    "/search/code",
                    params={"q": q, "per_page": per_page, "page": page},
                )
                if page == 1:
                    total_count += int(data.get("total_count", 0))

                items = data.get("items", [])
                page_items = [entry for entry in items if isinstance(entry, dict)]
                if not page_items:
                    break
                all_items.extend(page_items)
                if len(page_items) < per_page:
                    break
                page += 1

        sliced = all_items[max(0, skip) : max(0, skip) + max(1, take)]
        results: list[str] = []
        for item in sliced:
            repository = item.get("repository") or {}
            repo_name = str(repository.get("name") or "")
            path = str(item.get("path") or "")
            results.append(f"{repo_name}:/{path}" if repo_name else f"/{path}")

        if not total_count:
            total_count = len(all_items)
        return {"matchesCount": total_count, "results": results}

    def _get_repository_files(
        self: Any,
        *,
        repo: str,
        path: str | None,
        branch: str | None,
    ) -> list[dict[str, Any]]:
        normalized_path = normalize_path(path)
        ref = normalize_branch_name(branch) or self._get_repository_default_branch(repo)
        prefix = normalized_path.strip("/")
        repo_prefix = self._repo_prefix(repo)

        def _tree_entries(treeish: str, *, recursive: bool) -> list[dict[str, Any]]:
            encoded_treeish = quote(treeish, safe="")
            params = {"recursive": "1"} if recursive else None
            data = self._request_json(
                "GET",
                f"{repo_prefix}/git/trees/{encoded_treeish}",
                params=params,
            )
            entries = data.get("tree", [])
            if not isinstance(entries, list):
                return []
            return [item for item in entries if isinstance(item, dict)]

        def _to_file_entry(entry_path: str, *, sha: str | None) -> dict[str, Any]:
            return {
                "path": f"/{entry_path.lstrip('/')}",
                "is_binary": False,
                "sha": sha,
            }

        if not prefix:
            root_tree = _tree_entries(ref, recursive=True)
            output: list[dict[str, Any]] = []
            for item in root_tree:
                if item.get("type") != "blob":
                    continue
                output.append(
                    _to_file_entry(
                        str(item.get("path") or ""),
                        sha=str(item.get("sha") or "") or None,
                    )
                )
            return output

        path_parts = [part for part in prefix.split("/") if part]
        current_entries = _tree_entries(ref, recursive=False)
        resolved_parts: list[str] = []

        for index, part in enumerate(path_parts):
            matched = next(
                (item for item in current_entries if str(item.get("path") or "") == part),
                None,
            )
            if not isinstance(matched, dict):
                return []

            node_type = str(matched.get("type") or "")
            resolved_parts.append(part)
            is_last = index == len(path_parts) - 1

            if is_last and node_type == "blob":
                return [
                    _to_file_entry(
                        "/".join(resolved_parts),
                        sha=str(matched.get("sha") or "") or None,
                    )
                ]

            if node_type != "tree":
                return []

            tree_sha = str(matched.get("sha") or "").strip()
            if not tree_sha:
                return []

            if is_last:
                subtree = _tree_entries(tree_sha, recursive=True)
                subtree_prefix = "/".join(resolved_parts)
                output = []
                for item in subtree:
                    if item.get("type") != "blob":
                        continue
                    rel_path = str(item.get("path") or "")
                    full_path = f"{subtree_prefix}/{rel_path}" if rel_path else subtree_prefix
                    output.append(
                        _to_file_entry(
                            full_path,
                            sha=str(item.get("sha") or "") or None,
                        )
                    )
                return output

            current_entries = _tree_entries(tree_sha, recursive=False)

        return []

    def _get_file_text(
        self: Any,
        *,
        repo: str,
        file_path: str,
        branch: str | None,
        blob_sha: str | None = None,
        session: requests.Session | None = None,
    ) -> str:
        if blob_sha:
            try:
                blob_data = self._request_json(
                    "GET",
                    f"{self._repo_prefix(repo)}/git/blobs/{quote(blob_sha, safe='')}",
                    session=session,
                )
                blob_content = blob_data.get("content")
                blob_encoding = str(blob_data.get("encoding") or "")
                if isinstance(blob_content, str) and blob_encoding.lower() == "base64":
                    decoded = base64.b64decode(blob_content.encode("utf-8"))
                    return decoded.decode("utf-8", errors="replace")
                if isinstance(blob_content, str):
                    return blob_content
            except Exception as exc:
                logger.debug(
                    "Blob fetch failed for %s sha=%s, falling back to contents API: %s",
                    file_path,
                    blob_sha,
                    exc,
                )

        ref = normalize_branch_name(branch) or self._get_repository_default_branch(repo)
        encoded_path = quote(file_path.lstrip("/"), safe="/")
        data = self._request_json(
            "GET",
            f"{self._repo_prefix(repo)}/contents/{encoded_path}",
            params={"ref": ref},
            session=session,
        )
        content = data.get("content")
        encoding = str(data.get("encoding") or "")
        if isinstance(content, str) and encoding.lower() == "base64":
            decoded = base64.b64decode(content.encode("utf-8"))
            return decoded.decode("utf-8", errors="replace")
        if isinstance(content, str):
            return content
        return self._request_text(
            "GET",
            f"{self._repo_prefix(repo)}/contents/{encoded_path}",
            params={"ref": ref},
            session=session,
        )

    @staticmethod
    def _default_grep_workers(candidate_files: int) -> int:
        if candidate_files <= 1:
            return 1
        if candidate_files <= 8:
            return 4
        if candidate_files <= 32:
            return 8
        return 8

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

    def _git_subprocess(self: Any, args: list[str], *, cwd: str | None = None) -> None:
        subprocess.run(
            args,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )

    def _git_subprocess_result(
        self: Any,
        args: list[str],
        *,
        cwd: str | None = None,
        check: bool = True,
    ) -> Any:
        return subprocess.run(
            args,
            cwd=cwd,
            check=check,
            capture_output=True,
            text=True,
        )

    def _github_grep_cache_root(self: Any) -> str:
        configured = (os.getenv("SMITH_GITHUB_GREP_CACHE_DIR") or "").strip()
        if configured:
            return configured
        return str(Path.home() / ".cache" / "smith" / "github-grep")

    def _github_grep_cache_max_age_seconds(self: Any) -> int:
        return parse_int_env(
            "GITHUB_GREP_CACHE_MAX_AGE_SECONDS",
            default=30,
            min_value=1,
            max_value=86_400,
        )

    def _local_checkout_path(self: Any, *, org: str, repo: str, branch: str) -> str:
        root = self._github_grep_cache_root()
        return os.path.join(
            root,
            self._sanitize_cache_component(org),
            self._sanitize_cache_component(repo),
            self._sanitize_cache_component(branch),
        )

    def _local_checkout_remote_url(self: Any, *, org: str, repo: str) -> str:
        return f"https://github.com/{quote(org, safe='')}/{quote(repo, safe='')}.git"

    def _local_checkout_needs_refresh(self: Any, checkout_dir: str) -> bool:
        marker = os.path.join(checkout_dir, ".smith_last_fetch")
        if not os.path.isfile(marker):
            return True
        max_age = self._github_grep_cache_max_age_seconds()
        age_seconds = time.time() - os.path.getmtime(marker)
        return age_seconds >= max_age

    def _mark_local_checkout_refreshed(self: Any, checkout_dir: str) -> None:
        marker = os.path.join(checkout_dir, ".smith_last_fetch")
        Path(marker).touch()

    @staticmethod
    def _compute_sparse_patterns(path: str | None, glob: str | None) -> list[str] | None:
        return _local_checkout.compute_sparse_patterns(path, glob)

    def _apply_sparse_patterns(
        self: Any,
        checkout_dir: str,
        patterns: list[str] | None,
    ) -> None:
        _local_checkout.apply_sparse_patterns(self._git_subprocess, checkout_dir, patterns)

    def _git_subprocess_output(self: Any, args: list[str], *, cwd: str | None = None) -> str:
        result = subprocess.run(
            args,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def _remote_head_sha(self: Any, checkout_dir: str, branch: str) -> str | None:
        return _local_checkout.remote_head_sha(self._git_subprocess_output, checkout_dir, branch)

    def _local_head_sha(self: Any, checkout_dir: str) -> str | None:
        return _local_checkout.local_head_sha(self._git_subprocess_output, checkout_dir)

    def _ensure_local_checkout(
        self: Any,
        *,
        repo: str,
        branch: str,
        sparse_patterns: list[str] | None = None,
    ) -> str | None:
        if not parse_bool_env("GITHUB_GREP_USE_LOCAL_CACHE", default=True):
            return None

        org = self._require_github_org()
        checkout_dir = self._local_checkout_path(org=org, repo=repo, branch=branch)
        checkout_lock = self._cache_lock(checkout_dir)
        remote_url = self._local_checkout_remote_url(org=org, repo=repo)
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
                        ["--depth", "1", "--branch", branch, remote_url, checkout_dir]
                    )

                    self._git_subprocess(clone_args)
                    self._apply_sparse_patterns(checkout_dir, sparse_patterns)
                    self._mark_local_checkout_refreshed(checkout_dir)
                    return checkout_dir

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
                    self._git_subprocess(fetch_args)
                    self._git_subprocess(["git", "-C", checkout_dir, "checkout", "--force", "FETCH_HEAD"])
                    self._apply_sparse_patterns(checkout_dir, sparse_patterns)
                    self._mark_local_checkout_refreshed(checkout_dir)
                    return checkout_dir

                self._apply_sparse_patterns(checkout_dir, sparse_patterns)
                return checkout_dir
            except Exception as exc:
                logger.debug("Local checkout unavailable for %s@%s, using API fallback: %s", repo, branch, exc)
                return None

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

    def _get_local_repository_files(
        self: Any,
        *,
        checkout_dir: str,
        path: str | None,
    ) -> list[dict[str, Any]]:
        normalized_path = normalize_path(path)
        prefix = normalized_path.strip("/")

        if prefix:
            target = os.path.join(checkout_dir, prefix)
            if os.path.isfile(target):
                rel = prefix.replace(os.sep, "/")
                return [{"path": f"/{rel.lstrip('/')}", "is_binary": False, "sha": None, "local_path": target}]
            if not os.path.isdir(target):
                return []
            roots = [target]
        else:
            roots = [checkout_dir]

        output: list[dict[str, Any]] = []
        for root in roots:
            for current_root, dirnames, filenames in os.walk(root):
                dirnames[:] = [dirname for dirname in dirnames if dirname != ".git"]
                for filename in filenames:
                    full_path = os.path.join(current_root, filename)
                    rel = os.path.relpath(full_path, checkout_dir).replace(os.sep, "/")
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
        regex_pattern = pattern or ".*"
        is_match_all = match_all_pattern(regex_pattern)
        file_regex = glob_to_regex(glob) if glob else ".*"
        filename_filter = re.compile(file_regex)
        resolved_branch = normalize_branch_name(branch) or self._get_repository_default_branch(repo)
        grep_local_cache_enabled = parse_bool_env(
            "GITHUB_GREP_USE_LOCAL_CACHE",
            default=True,
        )
        use_local_cache = grep_local_cache_enabled and not no_clone
        sparse_patterns = self._compute_sparse_patterns(path, glob) if use_local_cache else None
        checkout_dir: str | None = None
        if use_local_cache:
            checkout_dir = self._ensure_local_checkout(
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
            files = self._get_repository_files(repo=repo, path=path, branch=resolved_branch)
        matching = [
            item
            for item in files
            if filename_filter.search(os.path.basename(str(item.get("path", ""))))
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

        search_pattern, compile_error = compile_search_pattern(
            regex_pattern,
            case_insensitive=case_insensitive,
        )
        if compile_error or search_pattern is None:
            return grep_compile_error_result(compile_error or "Invalid pattern")

        output_lines: list[str] = []
        warnings: list[str] = []
        files_matched = 0

        grep_parallel_enabled = parse_bool_env(
            "GITHUB_GREP_ENABLE_PARALLEL",
            default=True,
        )
        grep_max_workers = parse_int_env(
            "GITHUB_GREP_MAX_WORKERS",
            default=self._default_grep_workers(len(matching)),
            min_value=1,
            max_value=32,
        )

        def _process_file(
            file_path: str,
            blob_sha: str | None,
            local_path: str | None,
            *,
            session: requests.Session | None = None,
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
        ]
        if reverse:
            file_entries.reverse()
        if checkout_dir:
            effective_workers = min(
                grep_max_workers,
                len(file_entries) or 1,
            )
        else:
            effective_workers = min(
                grep_max_workers,
                self._config.github_max_concurrent_requests,
                len(file_entries) or 1,
            )
        use_parallel = grep_parallel_enabled and effective_workers > 1 and len(matching) > 1
        if use_parallel:
            max_workers = max(1, effective_workers)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
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
