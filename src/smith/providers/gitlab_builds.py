from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from smith.providers.helpers import grep_build_logs_core

if TYPE_CHECKING:
    pass


class GitLabBuildMixin:
    def get_build_log(self: Any, *, repo: str, build_id: int) -> dict[str, Any]:
        pipeline = self._request_json("GET", f"/projects/{self._project_id(repo)}/pipelines/{build_id}")
        jobs = self._get_paginated_list(
            f"/projects/{self._project_id(repo)}/pipelines/{build_id}/jobs"
        )

        mapped_jobs = []
        for item in jobs:
            if not isinstance(item, dict):
                continue
            mapped_jobs.append(
                {
                    "id": item.get("id"),
                    "type": "job",
                    "created_on": item.get("started_at") or item.get("created_at"),
                    "line_count": None,
                    "url": item.get("web_url"),
                    "stage_name": item.get("stage"),
                    "job_name": item.get("name"),
                    "step_name": None,
                }
            )

        metadata = {
            "project_name": self._project_namespace(repo),
            "build_id": build_id,
            "build_number": pipeline.get("iid") or pipeline.get("id"),
            "status": pipeline.get("status"),
            "result": pipeline.get("status"),
            "definition_name": pipeline.get("name") or pipeline.get("ref") or "pipeline",
            "repository_name": self._project_short_name(repo),
            "branch": pipeline.get("ref"),
            "commit": pipeline.get("sha"),
        }
        return {"metadata": metadata, "logs": mapped_jobs}

    def get_build_log_content(
        self: Any,
        *,
        repo: str,
        log_id: int,
    ) -> str:
        return self._request_text(
            "GET",
            f"/projects/{self._project_id(repo)}/jobs/{log_id}/trace",
        )

    def grep_build_log(
        self: Any,
        *,
        repo: str,
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
            build_logs = self.get_build_log(repo=repo, build_id=build_id)
            resolved_log_ids = [
                int(item["id"])
                for item in build_logs.get("logs", [])
                if isinstance(item, dict) and item.get("id") is not None
            ]

        def _get_content(lid: int) -> str:
            return self.get_build_log_content(repo=repo, log_id=lid)

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
