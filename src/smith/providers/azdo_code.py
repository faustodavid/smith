from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any, Literal

from smith.formatting import glob_to_regex, normalize_branch_name, truncate_output
from smith.providers.helpers import (
    build_grep_result,
    grep_compile_error_result,
    grep_match_lines,
)
from smith.utils import (
    compile_search_pattern,
    match_all_pattern,
    normalize_path,
    slice_lines,
)

if TYPE_CHECKING:
    pass


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
    ) -> dict[str, Any]:
        folder_path = normalize_path(path)
        normalized_branch = normalize_branch_name(branch)
        regex_pattern = pattern or ".*"
        is_match_all = match_all_pattern(regex_pattern)

        file_regex = glob_to_regex(glob) if glob else ".*"
        filename_filter = re.compile(file_regex)

        files = self._get_repository_files(
            project=project,
            repo=repo,
            path=folder_path,
            branch=normalized_branch,
        )

        matching = [
            {
                "path": file_item.get("path", ""),
                "is_binary": bool(
                    file_item.get("isBinary")
                    or (file_item.get("contentMetadata") or {}).get("isBinary")
                ),
            }
            for file_item in files
            if filename_filter.search(os.path.basename(str(file_item.get("path", ""))))
        ]

        if output_mode == "files_with_matches" and is_match_all:
            text = "\n".join(item["path"] for item in matching)
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
            file_path = file_item["path"]
            if file_item["is_binary"]:
                continue

            try:
                content = self._get_file_text(
                    project=project,
                    repo=repo,
                    file_path=file_path,
                    branch=normalized_branch,
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
