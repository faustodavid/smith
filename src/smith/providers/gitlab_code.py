from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote

from smith.errors import SmithApiError
from smith.formatting import glob_to_regex, normalize_branch_name, truncate_output
from smith.providers.helpers import build_grep_result, grep_compile_error_result, grep_match_lines
from smith.utils import compile_search_pattern, match_all_pattern, normalize_path, slice_lines

if TYPE_CHECKING:
    pass


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

        files = self._get_repository_files(
            repo=repo,
            path=folder_path,
            branch=normalized_branch,
        )

        matching = [
            {
                "path": file_item.get("path", ""),
                "is_binary": bool(file_item.get("is_binary")),
                "sha": file_item.get("sha"),
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
                content = self._get_file_text(
                    repo=repo,
                    file_path=file_path,
                    branch=normalized_branch,
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
