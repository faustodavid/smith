from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from smith.providers.helpers import paginate_results
from smith.utils import parse_iso_datetime

if TYPE_CHECKING:
    pass


class GitHubPullRequestMixin:
    @staticmethod
    def _pr_status(pr: dict[str, Any]) -> str:
        state = str(pr.get("state") or "").lower()
        if state == "open":
            return "active"
        if pr.get("merged_at"):
            return "completed"
        return "abandoned"

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
    def _search_issue_repo_name(issue: dict[str, Any]) -> str:
        repository_url = str(issue.get("repository_url") or "").rstrip("/")
        if not repository_url:
            return ""
        return repository_url.rsplit("/", 1)[-1]

    def _pull_request_row(
        self: Any,
        *,
        repo_name: str,
        item: dict[str, Any],
        status: str,
        include_labels: bool,
    ) -> dict[str, Any]:
        closed_dt = parse_iso_datetime(item.get("closed_at"))
        repository = (
            (item.get("base") or {}).get("repo")
            or (item.get("head") or {}).get("repo")
            or {}
        )
        labels: list[str] = []
        if include_labels:
            for label in item.get("labels") or []:
                if isinstance(label, dict):
                    name = str(label.get("name") or "").strip()
                    if name:
                        labels.append(name)

        return {
            "pr_id": item.get("number"),
            "title": item.get("title") or "",
            "created_by": str((item.get("user") or {}).get("login") or ""),
            "status": status,
            "creation_date": item.get("created_at"),
            "project_name": self._require_github_org(),
            "repository_name": repo_name,
            "repository_id": repository.get("id") or item.get("id"),
            "closed_date": (
                closed_dt.astimezone(UTC).strftime("%Y-%m-%d")
                if closed_dt
                else None
            ),
            "source_branch": item.get("head", {}).get("ref"),
            "target_branch": item.get("base", {}).get("ref"),
            "target_ref": item.get("base", {}).get("ref"),
            "labels": labels,
        }

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
        normalized_status = self._normalize_pr_statuses(statuses)

        repo_names = [item for item in (repos or []) if item]
        if not repo_names:
            repo_names = [entry["name"] for entry in self.list_repositories() if entry.get("name")]

        creator_filter = [item.lower() for item in creators or []]
        from_dt = parse_iso_datetime(date_from)
        to_dt = parse_iso_datetime(date_to)
        desired_count = max(1, max(0, skip) + max(1, take))
        single_repo_mode = len(repo_names) == 1

        states_to_fetch: list[str] = []
        if "active" in normalized_status:
            states_to_fetch.append("open")
        if any(item in normalized_status for item in {"completed", "abandoned"}):
            states_to_fetch.append("closed")
        if not states_to_fetch:
            states_to_fetch = ["all"]

        output: list[dict[str, Any]] = []
        for repo_name in repo_names:
            for github_state in states_to_fetch:
                page = 1
                per_page = 100
                while True:
                    pulls_data = self._request(
                        "GET",
                        f"{self._repo_prefix(repo_name)}/pulls",
                        params={"state": github_state, "per_page": per_page, "page": page},
                        expect_json=True,
                    )
                    if not isinstance(pulls_data, list):
                        break
                    pulls = [item for item in pulls_data if isinstance(item, dict)]
                    page_oldest_created: datetime | None = None
                    if not pulls:
                        break

                    for item in pulls:
                        status = self._pr_status(item)
                        if status not in normalized_status:
                            continue
                        if exclude_drafts and bool(item.get("draft")):
                            continue

                        user = item.get("user") or {}
                        creator = str(user.get("login") or "")
                        if creator_filter and not any(token in creator.lower() for token in creator_filter):
                            continue

                        created_dt = parse_iso_datetime(item.get("created_at"))
                        if created_dt and (page_oldest_created is None or created_dt < page_oldest_created):
                            page_oldest_created = created_dt
                        closed_dt = parse_iso_datetime(item.get("closed_at"))
                        reference_dt = closed_dt if status in {"completed", "abandoned"} else created_dt
                        if from_dt and reference_dt and reference_dt < from_dt:
                            continue
                        if to_dt and reference_dt and reference_dt > to_dt:
                            continue
                        output.append(
                            self._pull_request_row(
                                repo_name=repo_name,
                                item=item,
                                status=status,
                                include_labels=include_labels,
                            )
                        )

                    if single_repo_mode and len(output) >= desired_count:
                        break
                    if (
                        github_state == "open"
                        and from_dt
                        and page_oldest_created
                        and page_oldest_created < from_dt
                    ):
                        break
                    if len(pulls) < per_page:
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
        del project
        normalized_status = self._normalize_pr_statuses(statuses)

        org = self._require_github_org()
        repo_names = list(dict.fromkeys(str(item).strip().lower() for item in (repos or []) if str(item).strip()))
        repo_filter = set(repo_names)
        creator_filter = [item.lower() for item in creators or []]
        from_dt = parse_iso_datetime(date_from)
        to_dt = parse_iso_datetime(date_to)
        desired_count = max(0, skip) + max(1, take) + 1
        search_targets: list[str | None] = list(dict.fromkeys(repo_names)) if repo_names else [None]

        per_page = 100
        output: list[dict[str, Any]] = []
        seen_pull_requests: set[tuple[str, str]] = set()

        for repo_name in search_targets:
            qualifiers: list[str] = [query.strip(), "is:pr"]
            if repo_name:
                qualifiers.append(f"repo:{org}/{repo_name}")
            else:
                qualifiers.append(f"org:{org}")
            if normalized_status == ["active"]:
                qualifiers.append("is:open")
            elif all(status in {"completed", "abandoned"} for status in normalized_status):
                qualifiers.append("is:closed")

            page = 1
            target_matches = 0
            while True:
                data = self._request_json(
                    "GET",
                    "/search/issues",
                    params={
                        "q": " ".join(item for item in qualifiers if item),
                        "sort": "created",
                        "order": "desc",
                        "per_page": per_page,
                        "page": page,
                    },
                )
                issues = [item for item in data.get("items", []) if isinstance(item, dict)]
                if not issues:
                    break

                for issue in issues:
                    resolved_repo_name = self._search_issue_repo_name(issue)
                    normalized_repo_name = resolved_repo_name.lower()
                    if repo_filter and normalized_repo_name not in repo_filter:
                        continue

                    pr_info = issue.get("pull_request") or {}
                    pr_url = str(pr_info.get("url") or "").strip()
                    pr_number = issue.get("number")
                    if resolved_repo_name and pr_number is not None:
                        pr_path = f"{self._repo_prefix(resolved_repo_name)}/pulls/{pr_number}"
                    elif pr_url:
                        pr_path = pr_url
                    else:
                        continue

                    pr_detail = self._request_json("GET", pr_path)
                    merged_pr = dict(pr_detail)
                    merged_pr.setdefault("labels", issue.get("labels"))
                    merged_pr.setdefault("title", issue.get("title"))
                    merged_pr.setdefault("user", issue.get("user"))
                    merged_pr.setdefault("created_at", issue.get("created_at"))
                    merged_pr.setdefault("closed_at", issue.get("closed_at"))

                    status = self._pr_status(merged_pr)
                    if status not in normalized_status:
                        continue
                    if exclude_drafts and bool(merged_pr.get("draft")):
                        continue

                    creator = str((merged_pr.get("user") or {}).get("login") or "")
                    if creator_filter and not any(token in creator.lower() for token in creator_filter):
                        continue

                    created_dt = parse_iso_datetime(merged_pr.get("created_at"))
                    merged_dt = parse_iso_datetime(merged_pr.get("merged_at"))
                    closed_dt = parse_iso_datetime(merged_pr.get("closed_at"))
                    reference_dt = (merged_dt or closed_dt) if status in {"completed", "abandoned"} else created_dt
                    if from_dt and reference_dt and reference_dt < from_dt:
                        continue
                    if to_dt and reference_dt and reference_dt > to_dt:
                        continue

                    pr_key = (normalized_repo_name, str(merged_pr.get("number") or pr_number))
                    if pr_key in seen_pull_requests:
                        continue
                    seen_pull_requests.add(pr_key)

                    output.append(
                        self._pull_request_row(
                            repo_name=resolved_repo_name,
                            item=merged_pr,
                            status=status,
                            include_labels=include_labels,
                        )
                    )
                    target_matches += 1
                    if target_matches >= desired_count:
                        break

                if target_matches >= desired_count:
                    break
                if len(issues) < per_page:
                    break
                page += 1

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
        pr = self._request_json("GET", f"{self._repo_prefix(repo)}/pulls/{pull_request_id}")
        files = self._get_paginated_list(
            f"{self._repo_prefix(repo)}/pulls/{pull_request_id}/files",
            limit=2000,
        )
        changed_files = [str(item.get("filename")) for item in files if item.get("filename")]
        diffs = {
            str(item.get("filename")): str(item.get("patch") or "")
            for item in files
            if item.get("filename")
        }
        threads_data = self.get_pull_request_threads(repo=repo, pull_request_id=pull_request_id)
        mapped_pr = {
            "pullRequestId": pr.get("number"),
            "title": pr.get("title"),
            "status": self._pr_status(pr),
            "createdBy": {"displayName": (pr.get("user") or {}).get("login", "")},
            "sourceRefName": f"refs/heads/{(pr.get('head') or {}).get('ref', '')}",
            "targetRefName": f"refs/heads/{(pr.get('base') or {}).get('ref', '')}",
        }
        return {
            "pull_request": mapped_pr,
            "threads": threads_data.get("threads", []),
            "changed_files": changed_files,
            "diffs": diffs,
        }

    def get_pull_request_threads(
        self: Any,
        *,
        repo: str,
        pull_request_id: int,
        include_deleted: bool = False,
    ) -> dict[str, Any]:
        issue_comments = self._get_paginated_list(
            f"{self._repo_prefix(repo)}/issues/{pull_request_id}/comments"
        )
        review_comments = self._get_paginated_list(
            f"{self._repo_prefix(repo)}/pulls/{pull_request_id}/comments"
        )

        threads: list[dict[str, Any]] = []
        total_comments = 0

        for comment in issue_comments:
            if not isinstance(comment, dict):
                continue
            deleted = bool(comment.get("isDeleted", False))
            if deleted and not include_deleted:
                continue
            author = (comment.get("user") or {}).get("login", "")
            comment_payload = {
                "id": comment.get("id"),
                "author": author,
                "content": str(comment.get("body") or ""),
                "comment_type": "text",
                "is_deleted": deleted,
                "published_date": comment.get("created_at"),
                "last_updated_date": comment.get("updated_at"),
            }
            threads.append(
                {
                    "id": f"issue-{comment.get('id')}",
                    "status": None,
                    "is_deleted": deleted,
                    "file_path": None,
                    "line_start": None,
                    "line_end": None,
                    "comments": [comment_payload],
                    "comment_count": 1,
                    "published_date": comment.get("created_at"),
                    "last_updated_date": comment.get("updated_at"),
                }
            )
            total_comments += 1

        for comment in review_comments:
            if not isinstance(comment, dict):
                continue
            deleted = bool(comment.get("isDeleted", False))
            if deleted and not include_deleted:
                continue
            author = (comment.get("user") or {}).get("login", "")
            comment_payload = {
                "id": comment.get("id"),
                "author": author,
                "content": str(comment.get("body") or ""),
                "comment_type": "text",
                "is_deleted": deleted,
                "published_date": comment.get("created_at"),
                "last_updated_date": comment.get("updated_at"),
            }
            line = comment.get("line") or comment.get("original_line")
            threads.append(
                {
                    "id": f"review-{comment.get('id')}",
                    "status": None,
                    "is_deleted": deleted,
                    "file_path": f"/{str(comment.get('path') or '').lstrip('/')}" if comment.get("path") else None,
                    "line_start": line,
                    "line_end": line,
                    "comments": [comment_payload],
                    "comment_count": 1,
                    "published_date": comment.get("created_at"),
                    "last_updated_date": comment.get("updated_at"),
                }
            )
            total_comments += 1

        return {
            "pull_request_id": pull_request_id,
            "project_name": self._require_github_org(),
            "repository_name": repo,
            "returned_count": len(threads),
            "total_comments": total_comments,
            "threads": threads,
        }
