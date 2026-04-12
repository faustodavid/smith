from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from smith.providers.helpers import paginate_results
from smith.utils import parse_iso_datetime

if TYPE_CHECKING:
    pass


class GitLabPullRequestMixin:
    @staticmethod
    def _mr_status(merge_request: dict[str, Any]) -> str:
        state = str(merge_request.get("state") or "").lower()
        if state in {"opened", "open"}:
            return "active"
        if state == "merged":
            return "completed"
        return "abandoned"

    @staticmethod
    def _is_draft_merge_request(merge_request: dict[str, Any]) -> bool:
        if bool(merge_request.get("draft")):
            return True
        title = str(merge_request.get("title") or "").strip().lower()
        return title.startswith("draft:") or title.startswith("wip:")

    def list_pull_requests(
        self: Any,
        *,
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

        repo_names = [item for item in (repos or []) if item]
        if not repo_names:
            repo_names = [entry["name"] for entry in self.list_repositories() if entry.get("name")]

        creator_filter = [item.lower() for item in creators or []]
        from_dt = parse_iso_datetime(date_from)
        to_dt = parse_iso_datetime(date_to)
        window_size = max(1, take)
        desired_count = max(0, skip) + window_size + 1
        single_repo_mode = len(repo_names) == 1

        states_to_fetch: list[str] = []
        if "active" in normalized_status:
            states_to_fetch.append("opened")
        if "completed" in normalized_status:
            states_to_fetch.append("merged")
        if "abandoned" in normalized_status:
            states_to_fetch.append("closed")
        if not states_to_fetch:
            states_to_fetch = ["all"]
        elif single_repo_mode and len(states_to_fetch) > 1:
            states_to_fetch = ["all"]

        output: list[dict[str, Any]] = []
        for repo_name in repo_names:
            for gitlab_state in states_to_fetch:
                page = 1
                per_page = 100
                while True:
                    merge_requests_data = self._request(
                        "GET",
                        f"/projects/{self._project_id(repo_name)}/merge_requests",
                        params={"state": gitlab_state, "scope": "all", "per_page": per_page, "page": page},
                        expect_json=True,
                    )
                    if not isinstance(merge_requests_data, list):
                        break
                    merge_requests = [item for item in merge_requests_data if isinstance(item, dict)]
                    page_oldest_created: datetime | None = None
                    if not merge_requests:
                        break

                    for item in merge_requests:
                        status = self._mr_status(item)
                        if status not in normalized_status:
                            continue
                        if exclude_drafts and self._is_draft_merge_request(item):
                            continue

                        author = item.get("author") or {}
                        creator = str(author.get("username") or author.get("name") or "")
                        if creator_filter and not any(token in creator.lower() for token in creator_filter):
                            continue

                        created_dt = parse_iso_datetime(item.get("created_at"))
                        if created_dt and (page_oldest_created is None or created_dt < page_oldest_created):
                            page_oldest_created = created_dt
                        merged_dt = parse_iso_datetime(item.get("merged_at"))
                        closed_dt = parse_iso_datetime(item.get("closed_at"))
                        reference_dt = (merged_dt or closed_dt) if status in {"completed", "abandoned"} else created_dt
                        if from_dt and reference_dt and reference_dt < from_dt:
                            continue
                        if to_dt and reference_dt and reference_dt > to_dt:
                            continue

                        labels = [
                            str(label).strip()
                            for label in (item.get("labels") or [])
                            if str(label).strip()
                        ] if include_labels else []

                        resolved_closed_dt = merged_dt or closed_dt
                        output.append(
                            {
                                "pr_id": item.get("iid") or item.get("id"),
                                "title": item.get("title") or "",
                                "created_by": creator,
                                "status": status,
                                "creation_date": item.get("created_at"),
                                "project_name": self._project_namespace(repo_name),
                                "repository_name": self._project_short_name(repo_name),
                                "repository_id": item.get("project_id"),
                                "closed_date": (
                                    resolved_closed_dt.astimezone(UTC).strftime("%Y-%m-%d")
                                    if resolved_closed_dt is not None
                                    else None
                                ),
                                "source_branch": item.get("source_branch"),
                                "target_branch": item.get("target_branch"),
                                "target_ref": item.get("target_branch"),
                                "labels": labels,
                            }
                        )

                    if single_repo_mode and len(output) >= desired_count:
                        break
                    if (
                        gitlab_state == "opened"
                        and from_dt
                        and page_oldest_created
                        and page_oldest_created < from_dt
                    ):
                        break
                    if len(merge_requests) < per_page:
                        break
                    page += 1
                if single_repo_mode and len(output) >= desired_count:
                    break

        output.sort(key=lambda row: str(row.get("creation_date") or ""), reverse=True)
        paged = paginate_results(output, skip=skip, take=take)
        has_more = len(output) > max(0, skip) + len(paged)
        return {
            "returned_count": len(paged),
            "has_more": has_more,
            "results": paged,
        }

    def get_pull_request(
        self: Any,
        *,
        repo: str,
        pull_request_id: int,
    ) -> dict[str, Any]:
        merge_request = self._request_json(
            "GET",
            f"/projects/{self._project_id(repo)}/merge_requests/{pull_request_id}",
        )
        changes = self._request_json(
            "GET",
            f"/projects/{self._project_id(repo)}/merge_requests/{pull_request_id}/changes",
        )
        changed_files = [
            str(item.get("new_path") or item.get("old_path") or "")
            for item in changes.get("changes", [])
            if isinstance(item, dict) and str(item.get("new_path") or item.get("old_path") or "").strip()
        ]
        threads_data = self.get_pull_request_threads(repo=repo, pull_request_id=pull_request_id)
        mapped_pr = {
            "pullRequestId": merge_request.get("iid") or merge_request.get("id"),
            "title": merge_request.get("title"),
            "status": self._mr_status(merge_request),
            "createdBy": {
                "displayName": (merge_request.get("author") or {}).get("username")
                or (merge_request.get("author") or {}).get("name", "")
            },
            "sourceRefName": f"refs/heads/{merge_request.get('source_branch', '')}",
            "targetRefName": f"refs/heads/{merge_request.get('target_branch', '')}",
        }
        return {
            "pull_request": mapped_pr,
            "threads": threads_data.get("threads", []),
            "changed_files": changed_files,
        }

    def get_pull_request_threads(
        self: Any,
        *,
        repo: str,
        pull_request_id: int,
        include_deleted: bool = False,
    ) -> dict[str, Any]:
        discussions = self._get_paginated_list(
            f"/projects/{self._project_id(repo)}/merge_requests/{pull_request_id}/discussions"
        )

        threads: list[dict[str, Any]] = []
        total_comments = 0

        for discussion in discussions:
            if not isinstance(discussion, dict):
                continue

            notes = [note for note in discussion.get("notes", []) if isinstance(note, dict)]
            comments: list[dict[str, Any]] = []
            for note in notes:
                deleted = bool(note.get("is_deleted", False))
                if deleted and not include_deleted:
                    continue
                author = note.get("author") or {}
                comments.append(
                    {
                        "id": note.get("id"),
                        "author": str(author.get("username") or author.get("name") or ""),
                        "content": str(note.get("body") or ""),
                        "comment_type": "text",
                        "is_deleted": deleted,
                        "published_date": note.get("created_at"),
                        "last_updated_date": note.get("updated_at"),
                    }
                )

            if not comments:
                continue

            raw_position = discussion.get("position") or next(
                (
                    note.get("position")
                    for note in notes
                    if isinstance(note, dict) and isinstance(note.get("position"), dict)
                ),
                None,
            )
            position: dict[str, Any] = raw_position if isinstance(raw_position, dict) else {}
            file_path = str(position.get("new_path") or position.get("old_path") or "").strip()
            line = position.get("new_line") or position.get("old_line")

            threads.append(
                {
                    "id": discussion.get("id"),
                    "status": "resolved" if bool(discussion.get("resolved")) else None,
                    "is_deleted": False,
                    "file_path": f"/{file_path.lstrip('/')}" if file_path else None,
                    "line_start": line,
                    "line_end": line,
                    "comments": comments,
                    "comment_count": len(comments),
                    "published_date": comments[0].get("published_date"),
                    "last_updated_date": comments[-1].get("last_updated_date"),
                }
            )
            total_comments += len(comments)

        return {
            "pull_request_id": pull_request_id,
            "project_name": self._project_namespace(repo),
            "repository_name": self._project_short_name(repo),
            "returned_count": len(threads),
            "total_comments": total_comments,
            "threads": threads,
        }
