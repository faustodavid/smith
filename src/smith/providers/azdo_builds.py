from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from smith.formatting import normalize_branch_name
from smith.providers.helpers import grep_build_logs_core

if TYPE_CHECKING:
    pass


class AzdoBuildMixin:
    def get_build_log(self: Any, *, project: str, build_id: int) -> dict[str, Any]:
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
        self: Any,
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
        self: Any,
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
        if log_id is not None:
            resolved_log_ids = [log_id]
        else:
            build_logs = self.get_build_log(project=project, build_id=build_id)
            resolved_log_ids = [
                int(entry["id"])
                for entry in build_logs.get("logs", [])
                if isinstance(entry, dict) and entry.get("id") is not None
            ]

        def _get_content(lid: int) -> str:
            return self.get_build_log_content(
                project=project,
                build_id=build_id,
                log_id=lid,
                start_line=from_line,
                end_line=to_line,
            )

        return grep_build_logs_core(
            log_ids=resolved_log_ids,
            get_content=_get_content,
            pattern=pattern,
            output_mode=output_mode,
            case_insensitive=case_insensitive,
            context_lines=context_lines,
            from_line=from_line,
            to_line=to_line,
            max_output_chars=self.max_output_chars,
        )
