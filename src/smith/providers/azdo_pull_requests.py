from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from smith.formatting import normalize_branch_name
from smith.providers.helpers import paginate_results
from smith.utils import parse_iso_datetime

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class AzdoPullRequestMixin:
    @staticmethod
    def _normalize_pr_statuses(statuses: list[str] | None) -> list[str]:
        allowed_status = {"active", "completed", "abandoned"}
        effective_status = statuses or ["active", "completed", "abandoned"]
        normalized_status: list[str] = []
        for status in effective_status:
            lowered = status.strip().lower()
            if lowered not in allowed_status:
                raise ValueError("status must be one of: active, completed, abandoned")
            if lowered not in normalized_status:
                normalized_status.append(lowered)
        return normalized_status

    @staticmethod
    def _query_tokens(query: str | None) -> list[str]:
        return [token for token in str(query or "").strip().lower().split() if token]

    @classmethod
    def _matches_pr_query(cls, item: dict[str, Any], query: str | None) -> bool:
        tokens = cls._query_tokens(query)
        if not tokens:
            return True
        searchable = " ".join([
            str(item.get("title") or ""),
            str(item.get("description") or ""),
        ]).lower()
        return all(token in searchable for token in tokens)

    def _pull_request_row(
        self: Any,
        *,
        project_name: str,
        status: str,
        item: dict[str, Any],
        include_labels: bool,
    ) -> dict[str, Any]:
        repository = item.get("repository") or {}
        closed_dt = parse_iso_datetime(item.get("closedDate"))
        labels: list[str] = []
        if include_labels:
            for label in item.get("labels") or []:
                if isinstance(label, dict):
                    name = str(label.get("name") or "").strip()
                    if name:
                        labels.append(name)

        created_by = item.get("createdBy") or {}
        creator_display = str(created_by.get("displayName") or "")
        creator_unique = str(created_by.get("uniqueName") or "")
        return {
            "pr_id": item.get("pullRequestId"),
            "title": item.get("title") or "",
            "created_by": creator_display or creator_unique,
            "status": status,
            "creation_date": item.get("creationDate"),
            "project_name": project_name,
            "repository_name": str(repository.get("name") or ""),
            "repository_id": str(repository.get("id") or ""),
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

    def list_pull_requests(
        self: Any,
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
        normalized_status = self._normalize_pr_statuses(statuses)

        if projects:
            project_names = projects
        else:
            project_names = [entry["name"] for entry in self.list_projects() if entry.get("name")]

        repo_targets = list(dict.fromkeys(repo for repo in repos or [] if repo))
        repo_filter = {repo.lower() for repo in repo_targets}
        creator_filter = [creator.lower() for creator in creators or []]

        from_dt = parse_iso_datetime(date_from)
        to_dt = parse_iso_datetime(date_to)

        results: list[dict[str, Any]] = []

        for project_name in project_names:
            repo_scopes: list[str | None] = list(repo_targets) if repo_targets else [None]
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
                            results.append(
                                self._pull_request_row(
                                    project_name=project_name,
                                    status=status,
                                    item=item,
                                    include_labels=include_labels,
                                )
                            )

                        if len(items) < page_size:
                            break
                        local_skip += page_size

        total = len(results)
        results.sort(key=lambda row: str(row.get("creation_date") or ""), reverse=True)
        paged = paginate_results(results, skip=skip, take=take)
        has_more = total > max(0, skip) + len(paged)

        return {
            "returned_count": len(paged),
            "has_more": has_more,
            "results": paged,
        }

    def search_pull_requests(
        self: Any,
        *,
        query: str,
        project: str | None = None,
        repos: list[str] | None = None,
        statuses: list[str] | None = None,
        creators: list[str] | None = None,
        date_from: str | datetime | None = None,
        date_to: str | datetime | None = None,
        skip: int = 0,
        take: int = 20,
        exclude_drafts: bool = False,
        include_labels: bool = False,
    ) -> dict[str, Any]:
        if repos and not project:
            raise ValueError("Repository filter requires --project")
        normalized_status = self._normalize_pr_statuses(statuses)

        if project:
            project_names = [project]
        else:
            project_names = [entry["name"] for entry in self.list_projects() if entry.get("name")]

        repo_targets = list(dict.fromkeys(repo for repo in repos or [] if repo))
        repo_filter = {repo.lower() for repo in repo_targets}
        creator_filter = [creator.lower() for creator in creators or []]
        from_dt = parse_iso_datetime(date_from)
        to_dt = parse_iso_datetime(date_to)

        results: list[dict[str, Any]] = []

        for project_name in project_names:
            repo_scopes: list[str | None] = list(repo_targets) if repo_targets else [None]
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
                            if not self._matches_pr_query(item, query):
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

                            results.append(
                                self._pull_request_row(
                                    project_name=project_name,
                                    status=status,
                                    item=item,
                                    include_labels=include_labels,
                                )
                            )

                        if len(items) < page_size:
                            break
                        local_skip += page_size

        results.sort(key=lambda row: str(row.get("creation_date") or ""), reverse=True)
        paged = paginate_results(results, skip=skip, take=take)
        has_more = len(results) > max(0, skip) + len(paged)
        return {
            "returned_count": len(paged),
            "has_more": has_more,
            "results": paged,
        }

    def get_pull_request(
        self: Any,
        *,
        project: str,
        repo: str,
        pull_request_id: int,
    ) -> dict[str, Any]:
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
        except Exception as exc:
            logger.debug("Failed to fetch PR iteration changes for PR %d: %s", pull_request_id, exc)
            changed_files = []

        return {
            "pull_request": pr,
            "threads": threads,
            "changed_files": changed_files,
        }

    def get_pull_request_threads(
        self: Any,
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
