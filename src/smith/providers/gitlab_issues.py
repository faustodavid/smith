from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

if TYPE_CHECKING:
    pass


class GitLabIssueMixin:
    def _issue_to_work_item(self: Any, issue: dict[str, Any], repo: str) -> dict[str, Any]:
        labels = issue.get("labels") or []
        tag_names = [str(label).strip() for label in labels if str(label).strip()]
        state = "Closed" if str(issue.get("state") or "").lower() == "closed" else "Open"
        assignees = issue.get("assignees") or []
        assigned_to = None
        if isinstance(assignees, list):
            for assignee in assignees:
                if not isinstance(assignee, dict):
                    continue
                assigned_to = str(assignee.get("username") or assignee.get("name") or "").strip() or None
                if assigned_to:
                    break

        return {
            "id": issue.get("iid") or issue.get("id"),
            "title": issue.get("title"),
            "state": state,
            "type": "Issue",
            "project": self._require_gitlab_group(),
            "assigned_to": assigned_to,
            "tags": tag_names,
            "created_date": issue.get("created_at"),
            "changed_date": issue.get("updated_at"),
            "url": issue.get("web_url"),
            "repository": repo,
            "highlights": [],
        }

    def _issue_repo_name(self: Any, issue: dict[str, Any], *, explicit_repo: str | None) -> str:
        if explicit_repo:
            return explicit_repo

        references = issue.get("references") or {}
        if isinstance(references, dict):
            full_reference = str(references.get("full") or "").strip()
            if "#" in full_reference:
                project_ref = full_reference.split("#", 1)[0].strip().strip("/")
                if project_ref:
                    return self._relative_repo_path(project_ref)

        project_id = issue.get("project_id")
        if project_id is not None:
            full_path = self._project_path_from_id(project_id)
            if full_path:
                return self._relative_repo_path(full_path)

        return ""

    def get_ticket_by_id(
        self: Any,
        *,
        repo: str,
        work_item_id: int,
    ) -> dict[str, Any]:
        issue = self._request_json("GET", f"/projects/{self._project_id(repo)}/issues/{work_item_id}")
        state = "Closed" if str(issue.get("state") or "").lower() == "closed" else "Open"
        return {
            "id": issue.get("iid") or issue.get("id"),
            "url": issue.get("web_url"),
            "fields": {
                "System.WorkItemType": "Issue",
                "System.State": state,
                "System.Title": issue.get("title") or "",
            },
        }

    def search_work_items(
        self: Any,
        *,
        query: str,
        project: str | None = None,
        repo: str | None = None,
        state: str | None = None,
        assigned_to: str | None = None,
        skip: int = 0,
        take: int = 20,
        include_closed: bool = True,
    ) -> dict[str, Any]:
        repo_name = (repo or project or "").strip()
        start = max(0, skip)
        window_size = max(1, take)
        limit = start + window_size + 1

        params: dict[str, Any] = {"scope": "all"}
        if query.strip():
            params["search"] = query
        if not include_closed:
            params["state"] = "opened"
        elif state:
            lowered = state.strip().lower()
            if lowered in {"active", "open", "opened", "todo"}:
                params["state"] = "opened"
            elif lowered in {"closed", "done", "resolved"}:
                params["state"] = "closed"
        if assigned_to:
            params["assignee_username"] = "me" if assigned_to == "@me" else assigned_to

        if repo_name:
            path = f"/projects/{self._project_id(repo_name)}/issues"
        else:
            path = f"/groups/{quote(self._require_gitlab_group(), safe='')}/issues"

        issues_data = self._get_paginated_list(
            path,
            params=params,
            limit=limit,
        )
        items = [item for item in issues_data if isinstance(item, dict)] if isinstance(issues_data, list) else []
        paged = items[start : start + window_size]

        results = [
            self._issue_to_work_item(
                issue,
                self._issue_repo_name(issue, explicit_repo=repo_name),
            )
            for issue in paged
        ]
        has_more = len(items) > start + len(paged)
        matches_count = len(items)
        if has_more:
            matches_count = len(self._get_paginated_list(path, params=params))
        return {
            "matchesCount": matches_count,
            "returned_count": len(results),
            "has_more": has_more,
            "results": results,
        }

    def get_my_work_items(
        self: Any,
        *,
        project: str | None = None,
        repo: str | None = None,
        include_closed: bool = False,
        skip: int = 0,
        take: int = 20,
    ) -> dict[str, Any]:
        result = self.search_work_items(
            query="",
            project=project,
            repo=repo,
            assigned_to="@me",
            skip=skip,
            take=take,
            include_closed=include_closed,
        )
        return {
            "returned_count": result.get("returned_count", 0),
            "has_more": result.get("has_more", False),
            "results": result.get("results", []),
            "warnings": [],
        }
