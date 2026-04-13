from __future__ import annotations

from typing import TYPE_CHECKING, Any

from smith.providers.helpers import paginate_results

if TYPE_CHECKING:
    pass


class AzdoWorkItemMixin:
    def get_ticket_by_id(
        self: Any,
        *,
        project: str,
        work_item_id: int,
    ) -> dict[str, Any]:
        url = f"{self.org_url}/{project}/_apis/wit/workitems/{work_item_id}"
        params = {"$expand": "relations", "api-version": self.api_version}
        return self._request_json("GET", url, params=params)

    def list_work_items(
        self: Any,
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
        paged_ids = paginate_results(ids, skip=skip, take=take)
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
        self: Any,
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
        self: Any,
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

        aggregated = paginate_results(aggregated, skip=skip, take=take)
        return {
            "returned_count": len(aggregated),
            "has_more": False,
            "results": aggregated,
            "warnings": warnings,
        }
