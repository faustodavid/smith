from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import quote, urlparse

import requests
from azure.identity import DefaultAzureCredential

from smith.config import RuntimeConfig
from smith.errors import SmithAuthError
from smith.formatting import format_grep_matches, glob_to_regex, normalize_branch_name, truncate_output
from smith.providers.base import BaseProvider
from smith.utils import (
    compile_search_pattern,
    match_all_pattern,
    normalize_path,
    parse_iso_datetime,
    slice_lines,
)

ADO_SCOPE = "499b84ac-1321-427f-aa17-267ca6975798/.default"


class AzdoProvider(BaseProvider):
    def __init__(
        self,
        *,
        config: RuntimeConfig,
        credential: Any | None = None,
        session: requests.Session,
    ) -> None:
        super().__init__(config=config, session=session)
        self.org_url = config.org_url
        self.api_version = config.api_version
        self.max_output_chars = config.max_output_chars
        self.org_name = self._extract_org_name(self.org_url)
        self._credential = credential or DefaultAzureCredential(
            exclude_interactive_browser_credential=True
        )
        self._access_token: str | None = None

    @staticmethod
    def _extract_org_name(org_url: str) -> str:
        parsed = urlparse(org_url)
        path_bits = [bit for bit in parsed.path.split("/") if bit]
        if parsed.netloc == "dev.azure.com" and path_bits:
            return path_bits[0]

        host_bits = parsed.netloc.split(".")
        if host_bits and host_bits[0]:
            return host_bits[0]

        raise ValueError(f"Unable to infer organization name from URL: {org_url}")

    def _get_token(self, *, force_refresh: bool = False) -> str:
        if self._access_token and not force_refresh:
            return self._access_token

        try:
            token = self._credential.get_token(ADO_SCOPE)
        except Exception as exc:
            raise SmithAuthError(
                "Failed to acquire Azure DevOps token using DefaultAzureCredential. "
                "Run `az login` and retry."
            ) from exc

        self._access_token = token.token
        return self._access_token

    def _auth_error_message(self) -> str:
        return "Authentication rejected with HTTP 401/403. Run `az login` and retry."

    def _almsearch_url(self, suffix: str) -> str:
        return f"https://almsearch.dev.azure.com/{self.org_name}{suffix}"

    # ------------------------------------------------------------------
    # Projects & Repositories
    # ------------------------------------------------------------------

    def list_projects(self) -> list[dict[str, Any]]:
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

    def list_repositories(self, project: str) -> list[dict[str, Any]]:
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

    # ------------------------------------------------------------------
    # Code search & grep
    # ------------------------------------------------------------------

    def search_code(
        self,
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
        self,
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
        self,
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
        self,
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
        if compile_error:
            return {
                "text": compile_error,
                "files_matched": 0,
                "warnings": [],
                "partial": False,
            }

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

            match_line_nums = {
                idx for idx, line in enumerate(lines) if search_pattern.search(line)
            }
            if not match_line_nums:
                continue

            files_matched += 1
            if output_mode == "files_with_matches":
                output_lines.append(file_path)
                continue
            if output_mode == "count":
                output_lines.append(f"{file_path}:{len(match_line_nums)}")
                continue

            formatted = format_grep_matches(
                file_path,
                lines,
                match_line_nums,
                context_lines or 0,
                include_line_numbers=True,
            )
            output_lines.extend(formatted)

        text = "\n".join(output_lines)
        text = truncate_output(
            text,
            self.max_output_chars,
            "Use from_line/to_line to read specific ranges, or narrow with path/glob/pattern.",
        )
        return {
            "text": text,
            "files_matched": files_matched,
            "warnings": warnings,
            "partial": bool(warnings),
        }

    # ------------------------------------------------------------------
    # Pull requests
    # ------------------------------------------------------------------

    def list_pull_requests(
        self,
        *,
        projects: list[str] | None = None,
        repos: list[str] | None = None,
        statuses: list[str] | None = None,
        creators: list[str] | None = None,
        date_from: str | datetime | None = None,
        date_to: str | datetime | None = None,
        skip: int = 0,
        take: int = 100,
        exclude_drafts: bool = False,
        include_labels: bool = False,
    ) -> dict[str, Any]:
        allowed_status = {"active", "completed", "abandoned"}
        effective_status = statuses or ["active", "completed", "abandoned"]
        normalized_status: list[str] = []
        for status in effective_status:
            lowered = status.strip().lower()
            if lowered not in allowed_status:
                raise ValueError("status must be one of: active, completed, abandoned")
            if lowered not in normalized_status:
                normalized_status.append(lowered)

        if projects:
            project_names = projects
        else:
            project_names = [entry["name"] for entry in self.list_projects() if entry.get("name")]

        repo_targets = [repo for repo in repos or [] if repo]
        repo_filter = {repo.lower() for repo in repo_targets}
        creator_filter = [creator.lower() for creator in creators or []]

        from_dt = parse_iso_datetime(date_from)
        to_dt = parse_iso_datetime(date_to)

        results: list[dict[str, Any]] = []

        for project_name in project_names:
            repo_scopes: list[str | None] = repo_targets or [None]
            for repo_scope in repo_scopes:
                for status in normalized_status:
                    page_size = min(max(take, 1), 100)
                    local_skip = 0
                    if repo_scope:
                        url = (
                            f"{self.org_url}/{project_name}/_apis/git/repositories/"
                            f"{quote(str(repo_scope), safe='')}/pullrequests"
                        )
                    else:
                        url = f"{self.org_url}/{project_name}/_apis/git/pullrequests"
                    while True:
                        params: dict[str, Any] = {
                            "api-version": self.api_version,
                            "searchCriteria.status": status,
                            "$top": page_size,
                            "$skip": local_skip,
                        }
                        if include_labels:
                            params["searchCriteria.includeLabels"] = "true"
                        params["searchCriteria.queryTimeRangeType"] = (
                            "closed" if status in {"completed", "abandoned"} else "created"
                        )
                        if from_dt:
                            params["searchCriteria.minTime"] = from_dt.astimezone(UTC).strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            )
                        if to_dt:
                            params["searchCriteria.maxTime"] = to_dt.astimezone(UTC).strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            )

                        data = self._request_json("GET", url, params=params)
                        items = data.get("value", [])
                        if not isinstance(items, list) or not items:
                            break

                        for item in items:
                            if not isinstance(item, dict):
                                continue

                            if exclude_drafts and item.get("isDraft"):
                                continue

                            repository = item.get("repository") or {}
                            repository_name = str(repository.get("name") or "")
                            repository_id = str(repository.get("id") or "")
                            if repo_scope is None and repo_filter:
                                if (
                                    repository_name.lower() not in repo_filter
                                    and repository_id.lower() not in repo_filter
                                ):
                                    continue

                            created_by = item.get("createdBy") or {}
                            creator_display = str(created_by.get("displayName") or "")
                            creator_unique = str(created_by.get("uniqueName") or "")
                            if creator_filter:
                                source = f"{creator_display} {creator_unique}".lower()
                                if not any(token in source for token in creator_filter):
                                    continue

                            creation_dt = parse_iso_datetime(item.get("creationDate"))
                            closed_dt = parse_iso_datetime(item.get("closedDate"))
                            ref_dt = closed_dt if status in {"completed", "abandoned"} else creation_dt

                            if from_dt and ref_dt and ref_dt < from_dt:
                                continue
                            if to_dt and ref_dt and ref_dt > to_dt:
                                continue

                            labels: list[str] = []
                            if include_labels:
                                for label in item.get("labels") or []:
                                    if isinstance(label, dict):
                                        name = str(label.get("name") or "").strip()
                                        if name:
                                            labels.append(name)

                            results.append(
                                {
                                    "pr_id": item.get("pullRequestId"),
                                    "title": item.get("title") or "",
                                    "created_by": creator_display or creator_unique,
                                    "status": status,
                                    "creation_date": item.get("creationDate"),
                                    "project_name": project_name,
                                    "repository_name": repository_name,
                                    "repository_id": repository_id,
                                    "closed_date": (
                                        closed_dt.astimezone(UTC).strftime("%Y-%m-%d")
                                        if closed_dt
                                        else None
                                    ),
                                    "source_branch": normalize_branch_name(item.get("sourceRefName")),
                                    "target_branch": normalize_branch_name(item.get("targetRefName")),
                                    "target_ref": item.get("targetRefName"),
                                    "labels": labels,
                                }
                            )

                        if len(items) < page_size:
                            break
                        local_skip += page_size

        total = len(results)
        results.sort(key=lambda row: str(row.get("creation_date") or ""), reverse=True)
        paged = results[max(0, skip) : max(0, skip) + max(1, take)]
        has_more = total > max(0, skip) + len(paged)

        return {
            "returned_count": len(paged),
            "has_more": has_more,
            "results": paged,
        }

    def get_pull_request(self, *, project: str, repo: str, pull_request_id: int) -> dict[str, Any]:
        pr_url = (
            f"{self.org_url}/{project}/_apis/git/repositories/{repo}/pullrequests/"
            f"{pull_request_id}"
        )
        threads_url = (
            f"{self.org_url}/{project}/_apis/git/repositories/{repo}/pullrequests/"
            f"{pull_request_id}/threads"
        )
        iterations_url = (
            f"{self.org_url}/{project}/_apis/git/repositories/{repo}/pullrequests/"
            f"{pull_request_id}/iterations"
        )

        params = {"api-version": self.api_version}
        pr = self._request_json("GET", pr_url, params=params)
        threads_response = self._request_json("GET", threads_url, params=params)
        threads = threads_response.get("value", []) if isinstance(threads_response, dict) else []

        changed_files: list[str] = []
        try:
            iterations = self._request_json("GET", iterations_url, params=params).get("value", [])
            if iterations:
                latest_iteration = max(
                    [it.get("id", 0) for it in iterations if isinstance(it, dict)],
                    default=0,
                )
                if latest_iteration:
                    changes_url = (
                        f"{self.org_url}/{project}/_apis/git/repositories/{repo}/pullrequests/"
                        f"{pull_request_id}/iterations/{latest_iteration}/changes"
                    )
                    changes = self._request_json(
                        "GET",
                        changes_url,
                        params={"api-version": self.api_version, "$top": 2000},
                    )
                    for entry in changes.get("changeEntries", []):
                        if not isinstance(entry, dict):
                            continue
                        item = entry.get("item") or {}
                        path = item.get("path")
                        if path:
                            changed_files.append(path)
        except Exception:
            changed_files = []

        return {
            "pull_request": pr,
            "threads": threads,
            "changed_files": changed_files,
        }

    def get_pull_request_threads(
        self,
        *,
        project: str,
        repo: str,
        pull_request_id: int,
        include_deleted: bool = False,
    ) -> dict[str, Any]:
        threads_url = (
            f"{self.org_url}/{project}/_apis/git/repositories/{repo}/pullrequests/"
            f"{pull_request_id}/threads"
        )
        params = {"api-version": self.api_version}
        threads_response = self._request_json("GET", threads_url, params=params)
        raw_threads = threads_response.get("value", []) if isinstance(threads_response, dict) else []

        threads: list[dict[str, Any]] = []
        total_comments = 0

        for entry in raw_threads:
            if not isinstance(entry, dict):
                continue

            if bool(entry.get("isDeleted")) and not include_deleted:
                continue

            thread_context = entry.get("threadContext") or {}
            right_file_start = thread_context.get("rightFileStart") or {}
            right_file_end = thread_context.get("rightFileEnd") or {}

            comments: list[dict[str, Any]] = []
            for comment_entry in entry.get("comments", []):
                if not isinstance(comment_entry, dict):
                    continue
                if bool(comment_entry.get("isDeleted")) and not include_deleted:
                    continue

                author = comment_entry.get("author") or {}
                comments.append(
                    {
                        "id": comment_entry.get("id"),
                        "author": str(author.get("displayName") or author.get("uniqueName") or ""),
                        "content": str(comment_entry.get("content") or ""),
                        "comment_type": comment_entry.get("commentType"),
                        "is_deleted": bool(comment_entry.get("isDeleted")),
                        "published_date": comment_entry.get("publishedDate"),
                        "last_updated_date": comment_entry.get("lastUpdatedDate"),
                    }
                )

            total_comments += len(comments)
            threads.append(
                {
                    "id": entry.get("id"),
                    "status": entry.get("status"),
                    "is_deleted": bool(entry.get("isDeleted")),
                    "file_path": thread_context.get("filePath"),
                    "line_start": right_file_start.get("line"),
                    "line_end": right_file_end.get("line"),
                    "comments": comments,
                    "comment_count": len(comments),
                    "published_date": entry.get("publishedDate"),
                    "last_updated_date": entry.get("lastUpdatedDate"),
                }
            )

        return {
            "pull_request_id": pull_request_id,
            "project_name": project,
            "repository_name": repo,
            "returned_count": len(threads),
            "total_comments": total_comments,
            "threads": threads,
        }

    # ------------------------------------------------------------------
    # Builds
    # ------------------------------------------------------------------

    def get_build_log(self, *, project: str, build_id: int) -> dict[str, Any]:
        logs_url = f"{self.org_url}/{project}/_apis/build/builds/{build_id}/logs"
        build_url = f"{self.org_url}/{project}/_apis/build/builds/{build_id}"

        params = {"api-version": self.api_version}
        logs_data = self._request_json("GET", logs_url, params=params)
        build_data = self._request_json("GET", build_url, params=params)

        logs = []
        for log in logs_data.get("value", []):
            if not isinstance(log, dict):
                continue
            logs.append(
                {
                    "id": log.get("id"),
                    "type": log.get("type"),
                    "created_on": log.get("createdOn"),
                    "line_count": log.get("lineCount"),
                    "url": log.get("url"),
                }
            )

        metadata = {
            "project_name": project,
            "build_id": build_id,
            "build_number": build_data.get("buildNumber"),
            "status": str(build_data.get("status")).lower()
            if build_data.get("status") is not None
            else None,
            "result": str(build_data.get("result")).lower()
            if build_data.get("result") is not None
            else None,
            "definition_name": (build_data.get("definition") or {}).get("name"),
            "repository_name": (build_data.get("repository") or {}).get("name"),
            "branch": normalize_branch_name(build_data.get("sourceBranch")),
            "commit": build_data.get("sourceVersion"),
        }

        return {"metadata": metadata, "logs": logs}

    def get_build_log_content(
        self,
        *,
        project: str,
        build_id: int,
        log_id: int,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        url = f"{self.org_url}/{project}/_apis/build/builds/{build_id}/logs/{log_id}"
        params: dict[str, Any] = {"api-version": self.api_version}
        if start_line is not None:
            params["startLine"] = int(start_line)
        if end_line is not None:
            params["endLine"] = int(end_line)
        return self._request_text("GET", url, params=params)

    def grep_build_log(
        self,
        *,
        project: str,
        build_id: int,
        log_id: int | None = None,
        pattern: str | None = None,
        output_mode: Literal["content", "logs_with_matches", "count"] = "content",
        case_insensitive: bool = True,
        context_lines: int | None = 3,
        from_line: int | None = None,
        to_line: int | None = None,
    ) -> dict[str, Any]:
        regex_pattern = pattern or ".*"
        is_match_all = match_all_pattern(regex_pattern)

        if log_id is None and is_match_all:
            return {
                "text": (
                    "Error: Specify a log_id to read full content, or provide a pattern to search across all logs."
                ),
                "logs_matched": 0,
                "warnings": [],
                "partial": False,
            }

        search_pattern, compile_error = compile_search_pattern(
            regex_pattern,
            case_insensitive=case_insensitive,
        )
        if compile_error:
            return {
                "text": compile_error,
                "logs_matched": 0,
                "warnings": [],
                "partial": False,
            }

        warnings: list[str] = []

        if log_id is not None and is_match_all:
            if output_mode == "logs_with_matches":
                text = f"Log {log_id}"
                return {
                    "text": text,
                    "logs_matched": 1,
                    "warnings": [],
                    "partial": False,
                }
            if output_mode == "count":
                content = self.get_build_log_content(
                    project=project,
                    build_id=build_id,
                    log_id=log_id,
                    start_line=from_line,
                    end_line=to_line,
                )
                return {
                    "text": f"Log {log_id}:{len(content.splitlines())}",
                    "logs_matched": 1,
                    "warnings": [],
                    "partial": False,
                }

            content = self.get_build_log_content(
                project=project,
                build_id=build_id,
                log_id=log_id,
                start_line=from_line,
                end_line=to_line,
            )
            start = from_line or 1
            text = "\n".join(
                f"{start + idx}:{line}" for idx, line in enumerate(content.splitlines())
            )
            text = truncate_output(
                text,
                self.max_output_chars,
                "Use from_line/to_line to read specific ranges.",
            )
            return {
                "text": text,
                "logs_matched": 1,
                "warnings": [],
                "partial": False,
            }

        if log_id is not None:
            log_ids = [log_id]
        else:
            build_logs = self.get_build_log(project=project, build_id=build_id)
            log_ids = [
                int(entry["id"])
                for entry in build_logs.get("logs", [])
                if isinstance(entry, dict) and entry.get("id") is not None
            ]

        output_lines: list[str] = []
        logs_matched = 0

        for current_log_id in log_ids:
            try:
                content = self.get_build_log_content(
                    project=project,
                    build_id=build_id,
                    log_id=current_log_id,
                )
            except Exception as exc:
                warnings.append(f"failed to read Log {current_log_id}: {exc}")
                continue

            lines = slice_lines(
                content.splitlines(),
                from_line=from_line,
                to_line=to_line,
            )

            matches = {idx for idx, line in enumerate(lines) if search_pattern.search(line)}
            if not matches:
                continue

            logs_matched += 1
            if output_mode == "logs_with_matches":
                output_lines.append(f"Log {current_log_id}")
                continue
            if output_mode == "count":
                output_lines.append(f"Log {current_log_id}:{len(matches)}")
                continue

            output_lines.extend(
                format_grep_matches(
                    f"Log {current_log_id}",
                    lines,
                    matches,
                    context_lines or 0,
                    include_line_numbers=True,
                )
            )

        text = "\n".join(output_lines)
        text = truncate_output(
            text,
            self.max_output_chars,
            "Use from_line/to_line to read specific ranges, or narrow with pattern/log-id.",
        )

        return {
            "text": text,
            "logs_matched": logs_matched,
            "warnings": warnings,
            "partial": bool(warnings),
        }

    # ------------------------------------------------------------------
    # Work items (board)
    # ------------------------------------------------------------------

    def get_ticket_by_id(self, *, project: str, work_item_id: int) -> dict[str, Any]:
        url = f"{self.org_url}/{project}/_apis/wit/workitems/{work_item_id}"
        params = {"$expand": "relations", "api-version": self.api_version}
        return self._request_json("GET", url, params=params)

    def list_work_items(
        self,
        *,
        project: str,
        wiql: str,
        skip: int = 0,
        take: int = 20,
    ) -> dict[str, Any]:
        wiql_url = f"{self.org_url}/{project}/_apis/wit/wiql"
        wiql_data = self._request_json(
            "POST",
            wiql_url,
            params={"api-version": self.api_version},
            json_body={"query": wiql},
            headers={"Content-Type": "application/json"},
        )

        ids = [
            int(item["id"])
            for item in wiql_data.get("workItems", [])
            if isinstance(item, dict) and item.get("id") is not None
        ]

        total = len(ids)
        paged_ids = ids[max(0, skip) : max(0, skip) + max(1, take)]
        has_more = total > max(0, skip) + len(paged_ids)

        if not paged_ids:
            return {
                "total_count": total,
                "returned_count": 0,
                "has_more": has_more,
                "results": [],
            }

        results: list[dict[str, Any]] = []
        details_url = f"{self.org_url}/{project}/_apis/wit/workitems"
        for start in range(0, len(paged_ids), 200):
            chunk = paged_ids[start : start + 200]
            data = self._request_json(
                "GET",
                details_url,
                params={
                    "ids": ",".join(str(work_item_id) for work_item_id in chunk),
                    "$expand": "relations",
                    "api-version": self.api_version,
                },
            )
            for item in data.get("value", []):
                if isinstance(item, dict):
                    results.append(item)

        return {
            "total_count": total,
            "returned_count": len(results),
            "has_more": has_more,
            "results": results,
        }

    def search_work_items(
        self,
        *,
        query: str,
        project: str | None = None,
        area: str | None = None,
        work_item_type: str | None = None,
        state: str | None = None,
        assigned_to: str | None = None,
        skip: int = 0,
        take: int = 20,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "searchText": query,
            "$skip": max(0, skip),
            "$top": max(1, min(200, take)),
        }

        filters: dict[str, list[str]] = {}
        if project:
            filters["System.TeamProject"] = [project]
        if area:
            filters["System.AreaPath"] = [area]
        if work_item_type:
            filters["System.WorkItemType"] = [work_item_type]
        if state:
            filters["System.State"] = [state]
        if assigned_to:
            filters["System.AssignedTo"] = [assigned_to]

        if filters:
            payload["filters"] = filters

        url = self._almsearch_url(f"/_apis/search/workitemsearchresults?api-version={self.api_version}")
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
            fields = item.get("fields") or {}
            assigned = fields.get("System.AssignedTo")
            if isinstance(assigned, dict):
                assigned_name = assigned.get("displayName") or assigned.get("uniqueName")
            else:
                assigned_name = assigned

            tags_value = fields.get("System.Tags")
            tags: list[str] = []
            if isinstance(tags_value, str):
                tags = [tag.strip() for tag in tags_value.split(";") if tag.strip()]

            highlights: list[str] = []
            for match in item.get("matches") or item.get("hits") or []:
                if isinstance(match, dict):
                    value = (
                        match.get("value")
                        or match.get("snippet")
                        or match.get("text")
                        or match.get("highlights")
                    )
                    if isinstance(value, list):
                        highlights.extend(str(entry) for entry in value if entry)
                    elif value:
                        highlights.append(str(value))
                elif isinstance(match, str) and match:
                    highlights.append(match)

            results.append(
                {
                    "id": fields.get("System.Id"),
                    "title": fields.get("System.Title"),
                    "state": fields.get("System.State"),
                    "type": fields.get("System.WorkItemType"),
                    "project": fields.get("System.TeamProject"),
                    "assigned_to": assigned_name,
                    "tags": tags,
                    "created_date": fields.get("System.CreatedDate"),
                    "changed_date": fields.get("System.ChangedDate"),
                    "url": item.get("url"),
                    "highlights": highlights,
                }
            )

        matches_count = data.get("count", len(results))
        return {
            "matchesCount": matches_count,
            "returned_count": len(results),
            "has_more": matches_count > max(0, skip) + len(results),
            "results": results,
        }

    def get_my_work_items(
        self,
        *,
        project: str | None = None,
        include_closed: bool = False,
        skip: int = 0,
        take: int = 20,
    ) -> dict[str, Any]:
        state_filter = ""
        if not include_closed:
            state_filter = " AND [System.State] NOT IN ('Closed', 'Done', 'Removed')"

        if project:
            wiql = (
                "SELECT [System.Id], [System.Title], [System.State], [System.WorkItemType] "
                "FROM WorkItems "
                f"WHERE [System.TeamProject] = '{project}' "
                "AND [System.AssignedTo] = @Me"
                f"{state_filter} "
                "ORDER BY [System.ChangedDate] DESC"
            )
            return self.list_work_items(project=project, wiql=wiql, skip=skip, take=take)

        aggregated: list[dict[str, Any]] = []
        warnings: list[str] = []
        for project_entry in self.list_projects():
            project_name = project_entry.get("name")
            if not project_name:
                continue
            wiql = (
                "SELECT [System.Id], [System.Title], [System.State], [System.WorkItemType] "
                "FROM WorkItems "
                "WHERE [System.AssignedTo] = @Me"
                f"{state_filter} "
                "ORDER BY [System.ChangedDate] DESC"
            )
            try:
                result = self.list_work_items(
                    project=str(project_name),
                    wiql=wiql,
                    skip=0,
                    take=max(1, take),
                )
            except Exception as exc:
                warnings.append(f"{project_name}: {exc}")
                continue
            aggregated.extend(result.get("results", []))

        aggregated = aggregated[max(0, skip) : max(0, skip) + max(1, take)]
        return {
            "returned_count": len(aggregated),
            "has_more": False,
            "results": aggregated,
            "warnings": warnings,
        }
