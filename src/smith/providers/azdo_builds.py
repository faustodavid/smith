from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

from smith.formatting import normalize_branch_name
from smith.pipeline_listing import (
    JobRow,
    PipelineListQuery,
    build_job_row,
    build_pipeline_list_payload,
    build_pipeline_row,
    normalize_azdo_status,
)
from smith.providers.helpers import grep_build_logs_core
from smith.utils import parse_iso_datetime

if TYPE_CHECKING:  # pragma: no cover - import-time only
    pass


logger = logging.getLogger(__name__)


def _timeline_duration_seconds(record: dict[str, Any]) -> int | None:
    start = parse_iso_datetime(record.get("startTime"))
    finish = parse_iso_datetime(record.get("finishTime"))
    if start is None or finish is None:
        return None
    return max(0, int((finish - start).total_seconds()))


def _order_value(record: dict[str, Any]) -> int:
    try:
        return int(record.get("order") or 0)
    except (TypeError, ValueError):
        return 0


def _azdo_jobs_from_timeline(records: list[dict[str, Any]]) -> list[JobRow]:
    by_id = {
        rec.get("id"): rec
        for rec in records
        if isinstance(rec, dict) and rec.get("id")
    }

    def _stage_record(record: dict[str, Any]) -> dict[str, Any] | None:
        current: dict[str, Any] | None = record
        visited: set[Any] = set()
        while current is not None:
            current_id = current.get("id")
            if current_id in visited:
                return None
            visited.add(current_id)
            if str(current.get("type") or "").lower() == "stage":
                return current
            parent_id = current.get("parentId")
            if not parent_id:
                return None
            current = by_id.get(parent_id)
        return None

    stages: dict[Any, tuple[int, str | None, list[dict[str, Any]]]] = {}
    stageless_jobs: list[dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("type") or "").lower() != "job":
            continue
        stage = _stage_record(rec)
        if stage is None:
            stageless_jobs.append(rec)
            continue
        stage_id = stage.get("id")
        if stage_id not in stages:
            stages[stage_id] = (
                _order_value(stage),
                str(stage.get("name") or "").strip() or None,
                [],
            )
        stages[stage_id][2].append(rec)

    rows: list[JobRow] = []
    for _, stage_name, job_records in sorted(stages.values(), key=lambda item: item[0]):
        for rec in sorted(job_records, key=_order_value):
            rows.append(_azdo_job_row(rec, stage=stage_name))
    for rec in sorted(stageless_jobs, key=_order_value):
        rows.append(_azdo_job_row(rec, stage=None))
    return rows


def _azdo_job_row(record: dict[str, Any], *, stage: str | None) -> JobRow:
    status = normalize_azdo_status(record.get("state"), record.get("result"))
    result = str(record.get("result") or "").strip().lower()
    allow_failure = result == "succeededwithissues"
    return build_job_row(
        job_id=record.get("id"),
        name=record.get("name"),
        status=status,
        stage=stage,
        duration_s=_timeline_duration_seconds(record),
        allow_failure=allow_failure,
    )


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
        reverse: bool = False,
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

        def _get_content(lid: int) -> str | tuple[str, int]:
            content = self.get_build_log_content(
                project=project,
                build_id=build_id,
                log_id=lid,
                start_line=from_line,
                end_line=to_line,
            )
            if from_line is None and to_line is None:
                return content
            return content, ((from_line - 1) if from_line and from_line > 0 else 0)

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
            reverse=reverse,
        )

    def list_pipelines(
        self: Any,
        *,
        project: str,
        pipeline_id: int,
        query: PipelineListQuery,
    ) -> dict[str, Any]:
        build = self._request_json(
            "GET",
            f"{self.org_url}/{project}/_apis/build/builds/{pipeline_id}",
            params={"api-version": self.api_version},
        )
        start = parse_iso_datetime(build.get("startTime"))
        finish = parse_iso_datetime(build.get("finishTime"))
        duration_s: int | None = None
        if start is not None and finish is not None:
            duration_s = max(0, int((finish - start).total_seconds()))

        url: str | None = None
        links = build.get("_links")
        if isinstance(links, dict):
            web_link = links.get("web")
            if isinstance(web_link, dict):
                url = str(web_link.get("href") or "").strip() or None

        definition_name: str | None = None
        definition = build.get("definition")
        if isinstance(definition, dict):
            definition_name = str(definition.get("name") or "").strip() or None

        jobs = self._list_azdo_build_jobs(project=project, build_id=pipeline_id)

        row = build_pipeline_row(
            pipeline_id=build.get("id") or pipeline_id,
            iid=None,
            project=project,
            status=normalize_azdo_status(build.get("status"), build.get("result")),
            ref=normalize_branch_name(build.get("sourceBranch")),
            sha=build.get("sourceVersion"),
            name=definition_name,
            source=str(build.get("reason") or "").strip() or None,
            trigger_job=None,
            parent_id=None,
            depth=0,
            created_at=build.get("queueTime") or build.get("startTime"),
            duration_s=duration_s,
            url=url,
            jobs=jobs,
        )
        return build_pipeline_list_payload(rows=[row], query=query)

    def _list_azdo_build_jobs(
        self: Any, *, project: str, build_id: int
    ) -> list[JobRow]:
        try:
            payload = self._request_json(
                "GET",
                f"{self.org_url}/{project}/_apis/build/builds/{build_id}/timeline",
                params={"api-version": self.api_version},
            )
        except Exception as exc:  # noqa: BLE001 - timeline is best-effort
            logger.info(
                "Azure DevOps timeline fetch failed for build %s in %s: %s",
                build_id,
                project,
                exc,
            )
            return []
        if not isinstance(payload, dict):
            return []
        records = payload.get("records")
        if not isinstance(records, list):
            return []
        return _azdo_jobs_from_timeline([r for r in records if isinstance(r, dict)])
