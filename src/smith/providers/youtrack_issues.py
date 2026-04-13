from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from smith.errors import SmithApiError

_YOUTRACK_COLLECTION_PAGE_SIZE = 100
_YOUTRACK_ACTIVITY_CATEGORIES = [
    "AttachmentsCategory",
    "CommentAttachmentsCategory",
    "CommentTextCategory",
    "CommentVisibilityCategory",
    "CommentsCategory",
    "CustomFieldCategory",
    "DescriptionCategory",
    "IssueCreatedCategory",
    "IssueResolvedCategory",
    "IssueVisibilityCategory",
    "LinksCategory",
    "ProjectCategory",
    "SprintCategory",
    "SummaryCategory",
    "TagsCategory",
    "TotalVotesCategory",
    "VcsChangeCategory",
    "VcsChangeStateCategory",
    "VotersCategory",
]
_YOUTRACK_ISSUE_FIELDS = (
    "id,idReadable,summary,description,created,updated,resolved,votes,commentsCount,"
    "project(id,name,shortName),reporter(id,login,fullName),updater(id,login,fullName),"
    "tags(id,name),"
    "customFields($type,id,name,"
    "projectCustomField($type,id,field($type,id,name)),"
    "value($type,avatarUrl,color(id,background,foreground),fullName,id,isResolved,"
    "localizedName,login,minutes,name,presentation,text))"
)
_YOUTRACK_COMMENT_FIELDS = (
    "id,text,textPreview,created,updated,deleted,pinned,"
    "author(id,login,fullName),"
    "reactions(id,reaction,author(id,login,fullName)),"
    "attachments(id,name,created,size,mimeType,url,thumbnailURL,author(id,login,fullName))"
)
_YOUTRACK_ATTACHMENT_FIELDS = (
    "id,name,created,updated,size,mimeType,url,thumbnailURL,author(id,login,fullName)"
)
_YOUTRACK_LINK_FIELDS = (
    "id,direction,linkType(id,name),issues(id,idReadable,summary)"
)
_YOUTRACK_ACTIVITY_FIELDS = (
    "afterCursor,hasAfter,"
    "activities(id,$type,timestamp,"
    "author(id,login,fullName),"
    "category(id),"
    "field(id,name),"
    "target(id,idReadable,summary,text,$type),"
    "targetMember,"
    "added($type,id,idReadable,name,fullName,login,text,presentation,isResolved,"
    "localizedName,color(id,background,foreground)),"
    "removed($type,id,idReadable,name,fullName,login,text,presentation,isResolved,"
    "localizedName,color(id,background,foreground)))"
)


class YouTrackIssueMixin:
    @staticmethod
    def _format_timestamp(value: Any) -> str | None:
        if value in (None, ""):
            return None
        try:
            milliseconds = int(value)
        except (TypeError, ValueError):
            return None
        return datetime.fromtimestamp(milliseconds / 1000, tz=UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _user_payload(user: Any) -> dict[str, Any] | None:
        if not isinstance(user, dict):
            return None
        login = str(user.get("login") or "").strip()
        full_name = str(user.get("fullName") or user.get("name") or "").strip()
        if not login and not full_name:
            return None
        return {
            "id": user.get("id"),
            "login": login or None,
            "fullName": full_name or None,
        }

    @staticmethod
    def _user_display(user: Any) -> str | None:
        payload = YouTrackIssueMixin._user_payload(user)
        if not payload:
            return None
        full_name = str(payload.get("fullName") or "").strip()
        login = str(payload.get("login") or "").strip()
        if full_name and login:
            return f"{full_name} ({login})"
        return full_name or login or None

    @staticmethod
    def _query_value(value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            return ""
        if any(not char.isalnum() and char not in {"-", "_", "."} for char in normalized):
            return "{" + normalized + "}"
        return normalized

    def _request_list(
        self: Any,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> list[Any]:
        data = self._request("GET", path, params=params, expect_json=True)
        if isinstance(data, list):
            return data
        raise SmithApiError(f"Expected list response from {self._build_url(path)}")

    def _issue_url(self: Any, issue_id: str) -> str:
        return f"{self.youtrack_base_url}/issue/{quote(issue_id, safe='')}"

    def _absolute_url(self: Any, path: str | None) -> str | None:
        normalized = str(path or "").strip()
        if not normalized:
            return None
        if normalized.startswith("http://") or normalized.startswith("https://"):
            return normalized
        return f"{self.youtrack_base_url}{normalized}"

    def _field_value_to_text(self: Any, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, list):
            entries = [
                entry
                for entry in (self._field_value_to_text(item) for item in value)
                if entry
            ]
            return ", ".join(entries) if entries else None
        if isinstance(value, dict):
            if value.get("$type") == "FieldStyle":
                return None
            for key in ("presentation", "localizedName", "name", "fullName", "login", "text", "idReadable"):
                candidate = str(value.get(key) or "").strip()
                if candidate:
                    return candidate
            if "minutes" in value:
                try:
                    minutes = int(value["minutes"])
                except (TypeError, ValueError):
                    minutes = None
                if minutes is not None:
                    return f"{minutes}m"
            color = value.get("color")
            if isinstance(color, dict):
                candidate = str(color.get("id") or "").strip()
                if candidate:
                    return candidate
            return None
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value).strip() or None

    def _normalize_custom_fields(self: Any, custom_fields: Any) -> list[dict[str, Any]]:
        normalized_fields: list[dict[str, Any]] = []
        if not isinstance(custom_fields, list):
            return normalized_fields

        for custom_field in custom_fields:
            if not isinstance(custom_field, dict):
                continue
            name = str(custom_field.get("name") or "").strip()
            if not name:
                project_custom_field = custom_field.get("projectCustomField") or {}
                field = project_custom_field.get("field") if isinstance(project_custom_field, dict) else {}
                name = str(field.get("name") if isinstance(field, dict) else "" or "").strip()
            if not name:
                continue

            value = self._field_value_to_text(custom_field.get("value"))
            normalized_fields.append(
                {
                    "id": custom_field.get("id"),
                    "name": name,
                    "type": custom_field.get("$type"),
                    "value": value,
                }
            )

        return normalized_fields

    @staticmethod
    def _custom_field_map(custom_fields: list[dict[str, Any]]) -> dict[str, str | None]:
        return {
            str(field.get("name") or ""): cast_value
            for field in custom_fields
            if (cast_value := field.get("value", None)) is None or isinstance(cast_value, str)
        }

    def _normalize_attachment(self: Any, attachment: Any) -> dict[str, Any] | None:
        if not isinstance(attachment, dict):
            return None
        name = str(attachment.get("name") or "").strip()
        mime_type = str(attachment.get("mimeType") or "").strip()
        is_image = mime_type.lower().startswith("image/")
        return {
            "id": attachment.get("id"),
            "name": name or None,
            "mimeType": mime_type or None,
            "isImage": is_image,
            "size": attachment.get("size"),
            "created": self._format_timestamp(attachment.get("created")),
            "updated": self._format_timestamp(attachment.get("updated")),
            "url": self._absolute_url(attachment.get("url")),
            "thumbnailURL": self._absolute_url(attachment.get("thumbnailURL")),
            "author": self._user_payload(attachment.get("author")),
            "author_display": self._user_display(attachment.get("author")),
        }

    def _normalize_comment(self: Any, comment: Any) -> dict[str, Any] | None:
        if not isinstance(comment, dict):
            return None
        attachments = [
            normalized
            for normalized in (self._normalize_attachment(entry) for entry in comment.get("attachments") or [])
            if normalized
        ]
        text = str(comment.get("text") or comment.get("textPreview") or "").strip()

        reactions: list[dict[str, Any]] = []
        for reaction in comment.get("reactions") or []:
            if not isinstance(reaction, dict):
                continue
            reactions.append(
                {
                    "id": reaction.get("id"),
                    "reaction": str(reaction.get("reaction") or "").strip() or None,
                    "author": self._user_payload(reaction.get("author")),
                    "author_display": self._user_display(reaction.get("author")),
                }
            )

        return {
            "id": comment.get("id"),
            "text": text,
            "created": self._format_timestamp(comment.get("created")),
            "updated": self._format_timestamp(comment.get("updated")),
            "deleted": bool(comment.get("deleted", False)),
            "pinned": bool(comment.get("pinned", False)),
            "author": self._user_payload(comment.get("author")),
            "author_display": self._user_display(comment.get("author")),
            "attachments": attachments,
            "reactions": reactions,
        }

    def _normalize_link(self: Any, link: Any) -> dict[str, Any] | None:
        if not isinstance(link, dict):
            return None
        issues: list[dict[str, Any]] = []
        for related_issue in link.get("issues") or []:
            if not isinstance(related_issue, dict):
                continue
            related_issue_id = str(related_issue.get("idReadable") or related_issue.get("id") or "").strip()
            if not related_issue_id:
                continue
            issues.append(
                {
                    "id": related_issue_id,
                    "summary": str(related_issue.get("summary") or "").strip() or None,
                    "url": self._issue_url(related_issue_id),
                }
            )
        if not issues:
            return None
        link_type = link.get("linkType") or {}
        return {
            "id": link.get("id"),
            "type": str(link_type.get("name") if isinstance(link_type, dict) else "" or "").strip() or None,
            "direction": str(link.get("direction") or "").strip() or None,
            "issues": issues,
        }

    def _activity_value_to_text(self: Any, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, list):
            entries = [entry for entry in (self._activity_value_to_text(item) for item in value) if entry]
            if not entries:
                return None
            if len(entries) > 5:
                return f"{', '.join(entries[:5])}, +{len(entries) - 5} more"
            return ", ".join(entries)
        if isinstance(value, dict):
            for key in ("idReadable", "summary", "presentation", "localizedName", "name", "fullName", "login", "text"):
                candidate = str(value.get(key) or "").strip()
                if candidate:
                    return candidate
            return None
        return str(value).strip() or None

    def _normalize_activity(self: Any, activity: Any) -> dict[str, Any] | None:
        if not isinstance(activity, dict):
            return None
        category = activity.get("category") or {}
        field = activity.get("field") or {}
        category_id = str(category.get("id") if isinstance(category, dict) else "" or "").strip()
        field_name = str(field.get("name") if isinstance(field, dict) else "" or "").strip()
        target_member = str(activity.get("targetMember") or "").strip() or None
        added = self._activity_value_to_text(activity.get("added"))
        removed = self._activity_value_to_text(activity.get("removed"))
        action = self._activity_action(
            category_id=category_id,
            field_name=field_name,
            target_member=target_member,
            added=added,
            removed=removed,
        )
        return {
            "id": activity.get("id"),
            "timestamp": self._format_timestamp(activity.get("timestamp")),
            "author": self._user_payload(activity.get("author")),
            "author_display": self._user_display(activity.get("author")),
            "category": category_id or None,
            "field": field_name or target_member,
            "added": added,
            "removed": removed,
            "action": action,
            "type": activity.get("$type"),
        }

    @staticmethod
    def _activity_action(
        *,
        category_id: str,
        field_name: str,
        target_member: str | None,
        added: str | None,
        removed: str | None,
    ) -> str:
        display_field = field_name or target_member or "field"
        if category_id == "IssueCreatedCategory":
            return "created issue"
        if category_id == "DescriptionCategory":
            return "updated description"
        if category_id == "SummaryCategory":
            return "updated summary"
        if category_id in {"CommentsCategory", "CommentTextCategory"}:
            return "updated comments"
        if category_id in {"AttachmentsCategory", "CommentAttachmentsCategory"}:
            if added and not removed:
                return f"added attachment {added}"
            if removed and not added:
                return f"removed attachment {removed}"
            return "updated attachments"
        if category_id == "LinksCategory":
            if added and not removed:
                return f"linked {added} ({display_field})"
            if removed and not added:
                return f"unlinked {removed} ({display_field})"
            return f"updated links ({display_field})"
        if category_id in {"CustomFieldCategory", "IssueResolvedCategory", "TagsCategory", "ProjectCategory", "SprintCategory"}:
            if added and removed:
                return f"changed {display_field} from {removed} to {added}"
            if added:
                return f"set {display_field} to {added}"
            if removed:
                return f"cleared {display_field} (was {removed})"
        if added and removed:
            return f"changed {display_field} from {removed} to {added}"
        if added:
            return f"set {display_field} to {added}"
        if removed:
            return f"cleared {display_field} (was {removed})"
        return f"updated {display_field}"

    def _fetch_paginated_collection(
        self: Any,
        path: str,
        *,
        fields: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        all_items: list[dict[str, Any]] = []
        skip = 0
        while True:
            request_params = dict(params or {})
            request_params["fields"] = fields
            request_params["$top"] = _YOUTRACK_COLLECTION_PAGE_SIZE
            request_params["$skip"] = skip
            page = self._request_list(path, params=request_params)
            entries = [entry for entry in page if isinstance(entry, dict)]
            if not entries:
                break
            all_items.extend(entries)
            if len(entries) < _YOUTRACK_COLLECTION_PAGE_SIZE:
                break
            skip += len(entries)
        return all_items

    def _fetch_issue_comments(self: Any, issue_id: str) -> list[dict[str, Any]]:
        comments = self._fetch_paginated_collection(
            f"/issues/{quote(issue_id, safe='')}/comments",
            fields=_YOUTRACK_COMMENT_FIELDS,
        )
        return [
            normalized
            for normalized in (self._normalize_comment(comment) for comment in comments)
            if normalized
        ]

    def _fetch_issue_attachments(self: Any, issue_id: str) -> list[dict[str, Any]]:
        attachments = self._fetch_paginated_collection(
            f"/issues/{quote(issue_id, safe='')}/attachments",
            fields=_YOUTRACK_ATTACHMENT_FIELDS,
        )
        return [
            attachment
            for attachment in (self._normalize_attachment(entry) for entry in attachments)
            if attachment
        ]

    def _fetch_issue_links(self: Any, issue_id: str) -> list[dict[str, Any]]:
        links = self._fetch_paginated_collection(
            f"/issues/{quote(issue_id, safe='')}/links",
            fields=_YOUTRACK_LINK_FIELDS,
        )
        return [
            normalized
            for normalized in (self._normalize_link(link) for link in links)
            if normalized
        ]

    def _fetch_issue_timeline(self: Any, issue_id: str) -> list[dict[str, Any]]:
        activities: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {
                "fields": _YOUTRACK_ACTIVITY_FIELDS,
                "categories": _YOUTRACK_ACTIVITY_CATEGORIES,
                "$top": _YOUTRACK_COLLECTION_PAGE_SIZE,
            }
            if cursor:
                params["cursor"] = cursor
            page = self._request_json(
                "GET",
                f"/issues/{quote(issue_id, safe='')}/activitiesPage",
                params=params,
            )
            normalized_page = [
                activity
                for activity in (self._normalize_activity(entry) for entry in page.get("activities") or [])
                if activity
            ]
            activities.extend(normalized_page)
            if not bool(page.get("hasAfter")):
                break
            cursor = str(page.get("afterCursor") or "").strip() or None
            if not cursor:
                break
        return activities

    def _build_metadata(
        self: Any,
        *,
        issue: dict[str, Any],
        custom_fields: list[dict[str, Any]],
    ) -> dict[str, str]:
        field_map = self._custom_field_map(custom_fields)
        project = issue.get("project") or {}
        project_name = str(project.get("name") if isinstance(project, dict) else "" or "").strip()
        project_short_name = str(project.get("shortName") if isinstance(project, dict) else "" or "").strip()
        project_display = project_name
        if project_name and project_short_name:
            project_display = f"{project_name} ({project_short_name})"
        elif project_short_name:
            project_display = project_short_name

        metadata: dict[str, str] = {
            "Project": project_display or "-",
            "Type": field_map.get("Type") or "Issue",
            "State": field_map.get("State") or ("Closed" if issue.get("resolved") else "Open"),
            "Priority": field_map.get("Priority") or "-",
            "Reporter": self._user_display(issue.get("reporter")) or "-",
            "Updater": self._user_display(issue.get("updater")) or "-",
            "Created": self._format_timestamp(issue.get("created")) or "-",
            "Updated": self._format_timestamp(issue.get("updated")) or "-",
            "Resolved": self._format_timestamp(issue.get("resolved")) or "-",
            "Votes": str(issue.get("votes") or 0),
            "Comments": str(issue.get("commentsCount") or 0),
        }

        tags = issue.get("tags") or []
        if isinstance(tags, list):
            tag_names = [
                str(tag.get("name") or "").strip()
                for tag in tags
                if isinstance(tag, dict) and str(tag.get("name") or "").strip()
            ]
            if tag_names:
                metadata["Tags"] = ", ".join(tag_names)

        for custom_field in custom_fields:
            name = str(custom_field.get("name") or "").strip()
            if not name or name in metadata:
                continue
            metadata[name] = str(custom_field.get("value") or "-")

        return metadata

    def _search_query(
        self: Any,
        *,
        query: str,
        state: str | None,
        work_item_type: str | None,
        assigned_to: str | None,
        include_closed: bool,
    ) -> str:
        parts: list[str] = []
        if query.strip():
            parts.append(query.strip())
        if state:
            parts.append(f"State: {self._query_value(state)}")
        elif not include_closed:
            parts.append("#Unresolved")
        if work_item_type:
            parts.append(f"Type: {self._query_value(work_item_type)}")
        if assigned_to:
            assignee = "me" if assigned_to == "@me" else assigned_to
            parts.append(f"for: {self._query_value(assignee)}")
        return " ".join(part for part in parts if part.strip())

    def _search_result_to_work_item(self: Any, issue: dict[str, Any]) -> dict[str, Any]:
        custom_fields = self._normalize_custom_fields(issue.get("customFields"))
        field_map = self._custom_field_map(custom_fields)
        project = issue.get("project") or {}
        project_short_name = str(project.get("shortName") if isinstance(project, dict) else "" or "").strip()
        project_name = str(project.get("name") if isinstance(project, dict) else "" or "").strip()
        issue_id = str(issue.get("idReadable") or issue.get("id") or "").strip()
        tags = issue.get("tags") or []
        tag_names = [
            str(tag.get("name") or "").strip()
            for tag in tags
            if isinstance(tag, dict) and str(tag.get("name") or "").strip()
        ]

        return {
            "id": issue_id or issue.get("id"),
            "title": issue.get("summary"),
            "state": field_map.get("State") or ("Closed" if issue.get("resolved") else "Open"),
            "type": field_map.get("Type") or "Issue",
            "priority": field_map.get("Priority"),
            "project": project_short_name or project_name,
            "project_name": project_name or project_short_name or None,
            "assigned_to": field_map.get("Assignee"),
            "tags": tag_names,
            "created_date": self._format_timestamp(issue.get("created")),
            "changed_date": self._format_timestamp(issue.get("updated")),
            "url": self._issue_url(issue_id) if issue_id else None,
            "highlights": [],
        }

    def get_ticket_by_id(
        self: Any,
        *,
        work_item_id: int | str,
    ) -> dict[str, Any]:
        issue_id = str(work_item_id or "").strip()
        if not issue_id:
            raise ValueError("YouTrack issue ID is required.")

        issue = self._request_json(
            "GET",
            f"/issues/{quote(issue_id, safe='')}",
            params={"fields": _YOUTRACK_ISSUE_FIELDS},
        )
        custom_fields = self._normalize_custom_fields(issue.get("customFields"))

        description = str(issue.get("description") or "").strip()
        warnings: list[str] = []
        partial = False
        issue_attachments: list[dict[str, Any]] = []
        comments: list[dict[str, Any]] = []
        links: list[dict[str, Any]] = []
        timeline: list[dict[str, Any]] = []

        operations = {
            "attachments": lambda: self._fetch_issue_attachments(issue_id),
            "comments": lambda: self._fetch_issue_comments(issue_id),
            "links": lambda: self._fetch_issue_links(issue_id),
            "timeline": lambda: self._fetch_issue_timeline(issue_id),
        }

        with ThreadPoolExecutor(max_workers=len(operations)) as executor:
            futures = {
                executor.submit(operation): name
                for name, operation in operations.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    partial = True
                    warnings.append(f"Failed to fetch {name}: {exc}")
                    continue
                if name == "attachments":
                    issue_attachments = result
                elif name == "comments":
                    comments = result
                elif name == "links":
                    links = result
                elif name == "timeline":
                    timeline = result

        reactions = [
            {
                "comment_id": comment.get("id"),
                "reaction": reaction.get("reaction"),
                "author": reaction.get("author"),
                "author_display": reaction.get("author_display"),
            }
            for comment in comments
            for reaction in comment.get("reactions", [])
            if isinstance(comment, dict) and isinstance(reaction, dict)
        ]
        metadata = self._build_metadata(issue=issue, custom_fields=custom_fields)
        issue_id_readable = str(issue.get("idReadable") or issue_id).strip() or issue_id

        payload = {
            "provider": "youtrack",
            "id": issue_id_readable,
            "url": self._issue_url(issue_id_readable),
            "fields": {
                "System.WorkItemType": metadata.get("Type", "Issue"),
                "System.State": metadata.get("State", "Open"),
                "System.Title": str(issue.get("summary") or ""),
            },
            "title": str(issue.get("summary") or ""),
            "description": description,
            "project": {
                "id": (issue.get("project") or {}).get("id") if isinstance(issue.get("project"), dict) else None,
                "name": (issue.get("project") or {}).get("name") if isinstance(issue.get("project"), dict) else None,
                "shortName": (issue.get("project") or {}).get("shortName") if isinstance(issue.get("project"), dict) else None,
            },
            "reporter": self._user_payload(issue.get("reporter")),
            "updater": self._user_payload(issue.get("updater")),
            "created": self._format_timestamp(issue.get("created")),
            "updated": self._format_timestamp(issue.get("updated")),
            "resolved": self._format_timestamp(issue.get("resolved")),
            "votes": issue.get("votes"),
            "commentsCount": issue.get("commentsCount"),
            "tags": [
                str(tag.get("name") or "").strip()
                for tag in issue.get("tags") or []
                if isinstance(tag, dict) and str(tag.get("name") or "").strip()
            ],
            "metadata": metadata,
            "custom_fields": custom_fields,
            "attachments": issue_attachments,
            "comments": comments,
            "reactions": reactions,
            "links": links,
            "timeline": timeline,
        }
        if warnings:
            payload["warnings"] = warnings
        if partial:
            payload["partial"] = True
        return payload

    def search_work_items(
        self: Any,
        *,
        query: str,
        project: str | None = None,
        repo: str | None = None,
        state: str | None = None,
        assigned_to: str | None = None,
        work_item_type: str | None = None,
        skip: int = 0,
        take: int = 20,
        include_closed: bool = True,
    ) -> dict[str, Any]:
        del project, repo
        query_string = self._search_query(
            query=query,
            state=state,
            work_item_type=work_item_type,
            assigned_to=assigned_to,
            include_closed=include_closed,
        )
        fields = (
            "id,idReadable,summary,created,updated,resolved,"
            "project(name,shortName),"
            "tags(name),"
            "customFields(name,value($type,localizedName,name,fullName,login,presentation,isResolved))"
        )
        page_size = max(1, take) + 1
        params: dict[str, Any] = {
            "fields": fields,
            "$skip": max(0, skip),
            "$top": page_size,
            "customFields": ["Type", "State", "Priority", "Assignee"],
        }
        if query_string:
            params["query"] = query_string
        issues = self._request_list("/issues", params=params)
        items = [item for item in issues if isinstance(item, dict)]
        has_more = len(items) > max(1, take)
        paged = items[: max(1, take)]
        results = [self._search_result_to_work_item(issue) for issue in paged]
        matches_count = max(0, skip) + len(results) + (1 if has_more else 0)
        payload = {
            "provider": "youtrack",
            "matchesCount": matches_count,
            "matchesCountLowerBound": has_more,
            "returned_count": len(results),
            "has_more": has_more,
            "results": results,
        }
        return payload

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
            "provider": "youtrack",
            "matchesCount": result.get("matchesCount", 0),
            "matchesCountLowerBound": result.get("matchesCountLowerBound", False),
            "returned_count": result.get("returned_count", 0),
            "has_more": result.get("has_more", False),
            "results": result.get("results", []),
            "warnings": [],
        }
