from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


class GitHubIssueMixin:
    def _issue_to_work_item(self: Any, issue: dict[str, Any], repo: str) -> dict[str, Any]:
        labels = issue.get("labels") or []
        tag_names = [
            str(label.get("name"))
            for label in labels
            if isinstance(label, dict) and label.get("name")
        ]
        state = "Closed" if str(issue.get("state") or "").lower() == "closed" else "Open"
        return {
            "id": issue.get("number"),
            "title": issue.get("title"),
            "state": state,
            "type": "Issue",
            "project": self._require_github_org(),
            "assigned_to": ((issue.get("assignee") or {}).get("login") if issue.get("assignee") else None),
            "tags": tag_names,
            "created_date": issue.get("created_at"),
            "changed_date": issue.get("updated_at"),
            "url": issue.get("html_url"),
            "repository": repo,
            "highlights": [],
        }

    def get_ticket_by_id(
        self: Any,
        *,
        repo: str,
        work_item_id: int,
    ) -> dict[str, Any]:
        issue = self._request_json("GET", f"{self._repo_prefix(repo)}/issues/{work_item_id}")
        state = "Closed" if str(issue.get("state") or "").lower() == "closed" else "Open"
        return {
            "id": issue.get("number"),
            "url": issue.get("html_url"),
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
        org = self._require_github_org()
        repo_name = (repo or project or "").strip()

        qualifiers: list[str] = [query or "", f"org:{org}", "is:issue"]
        if repo_name:
            qualifiers.append(f"repo:{org}/{repo_name}")
        if not include_closed:
            qualifiers.append("is:open")
        elif state:
            lowered = state.strip().lower()
            if lowered in {"active", "open", "todo"}:
                qualifiers.append("is:open")
            elif lowered in {"closed", "done", "resolved"}:
                qualifiers.append("is:closed")
        if assigned_to:
            qualifiers.append(f"assignee:{assigned_to}")

        q = " ".join(item for item in qualifiers if item.strip())
        per_page = min(max(1, take), 100)
        page = (max(0, skip) // per_page) + 1
        data = self._request_json(
            "GET",
            "/search/issues",
            params={"q": q, "per_page": per_page, "page": page},
        )
        items = [item for item in data.get("items", []) if isinstance(item, dict)]
        offset = max(0, skip) % per_page
        paged = items[offset : offset + max(1, take)]

        results = []
        for issue in paged:
            repository_url = str(issue.get("repository_url") or "")
            repo_from_url = repository_url.rstrip("/").split("/")[-1] if repository_url else repo_name
            results.append(self._issue_to_work_item(issue, repo_from_url))

        total = int(data.get("total_count", len(results)))
        return {
            "matchesCount": total,
            "returned_count": len(results),
            "has_more": total > max(0, skip) + len(results),
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
