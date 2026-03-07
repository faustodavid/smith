from __future__ import annotations

import base64
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote

import requests

from smith.config import parse_bool_env, parse_int_env
from smith.formatting import format_grep_matches, glob_to_regex, normalize_branch_name, truncate_output
from smith.providers.helpers import (
    build_grep_result,
    grep_compile_error_result,
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


class GitHubCodeMixin:
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
        org = self._require_github_org()
        repos = self._get_paginated_list(f"/orgs/{quote(org, safe='')}/repos")
        return [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "defaultBranch": item.get("default_branch"),
                "webUrl": item.get("html_url"),
            }
            for item in repos
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
        org = self._require_github_org()
        effective_repos = [item for item in (repos or []) if item]
        if project and not effective_repos:
            effective_repos = [project]

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
            results.append(f"{org}/{repo_name}:/{path}")

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
        if candidate_files <= 96:
            return 16
        return 20

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
        regex_pattern = pattern or ".*"
        is_match_all = match_all_pattern(regex_pattern)
        file_regex = glob_to_regex(glob) if glob else ".*"
        filename_filter = re.compile(file_regex)
        resolved_branch = normalize_branch_name(branch) or self._get_repository_default_branch(repo)
        files = self._get_repository_files(repo=repo, path=path, branch=resolved_branch)
        matching = [
            item
            for item in files
            if filename_filter.search(os.path.basename(str(item.get("path", ""))))
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
        use_parallel = grep_parallel_enabled and grep_max_workers > 1 and len(matching) > 1

        def _process_file(
            file_path: str,
            blob_sha: str | None,
            *,
            session: requests.Session | None = None,
        ) -> tuple[list[str], int, str | None]:
            try:
                content = self._get_file_text(
                    repo=repo,
                    file_path=file_path,
                    branch=resolved_branch,
                    blob_sha=blob_sha,
                    session=session,
                )
            except Exception as exc:
                return [], 0, f"failed to read {file_path}: {exc}"

            lines = slice_lines(
                content.splitlines(),
                from_line=from_line,
                to_line=to_line,
            )

            match_line_nums = {idx for idx, line in enumerate(lines) if search_pattern.search(line)}
            if not match_line_nums:
                return [], 0, None

            if output_mode == "files_with_matches":
                return [file_path], 1, None
            if output_mode == "count":
                return [f"{file_path}:{len(match_line_nums)}"], 1, None
            return (
                format_grep_matches(
                    file_path,
                    lines,
                    match_line_nums,
                    context_lines or 0,
                    include_line_numbers=True,
                ),
                1,
                None,
            )

        def _process_file_in_worker(
            file_path: str,
            blob_sha: str | None,
        ) -> tuple[list[str], int, str | None]:
            return _process_file(
                file_path,
                blob_sha,
                session=self._get_http_session(),
            )

        file_entries = [
            (
                str(item.get("path", "")),
                str(item.get("sha") or "") or None,
            )
            for item in matching
        ]
        if use_parallel:
            max_workers = min(grep_max_workers, len(file_entries))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [
                    executor.submit(_process_file_in_worker, file_path, blob_sha)
                    for file_path, blob_sha in file_entries
                ]
                for (file_path, _blob_sha), future in zip(file_entries, futures):
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
            for file_path, blob_sha in file_entries:
                lines_out, matched_count, warning = _process_file(file_path, blob_sha)
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
