from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

from smith.pipeline_listing import (
    JobRow,
    PipelineListQuery,
    build_job_row,
    build_pipeline_list_payload,
    build_pipeline_row,
    normalize_github_status,
)
from smith.providers.helpers import grep_build_logs_core
from smith.utils import parse_iso_datetime

if TYPE_CHECKING:  # pragma: no cover - import-time only
    pass


logger = logging.getLogger(__name__)


def _github_job_duration_seconds(item: dict[str, Any]) -> int | None:
    started = parse_iso_datetime(item.get("started_at"))
    completed = parse_iso_datetime(item.get("completed_at"))
    if started is None or completed is None:
        return None
    return max(0, int((completed - started).total_seconds()))


def _github_job_row(item: dict[str, Any]) -> JobRow:
    status = normalize_github_status(item.get("status"), item.get("conclusion"))
    return build_job_row(
        job_id=item.get("id"),
        name=item.get("name"),
        status=status,
        stage=None,
        duration_s=_github_job_duration_seconds(item),
        environment=item.get("environment") or None,
    )


class GitHubBuildMixin:
    def get_build_log(self: Any, *, repo: str, build_id: int) -> dict[str, Any]:
        run = self._request_json("GET", f"{self._repo_prefix(repo)}/actions/runs/{build_id}")
        jobs_data = self._request_json(
            "GET",
            f"{self._repo_prefix(repo)}/actions/runs/{build_id}/jobs",
            params={"per_page": 100, "page": 1},
        )
        jobs = []
        for item in jobs_data.get("jobs", []):
            if not isinstance(item, dict):
                continue
            jobs.append(
                {
                    "id": item.get("id"),
                    "type": "job",
                    "created_on": item.get("started_at"),
                    "line_count": None,
                    "url": item.get("url"),
                    "stage_name": item.get("name"),
                    "job_name": item.get("name"),
                    "step_name": None,
                }
            )

        metadata = {
            "project_name": self._require_github_org(),
            "build_id": build_id,
            "build_number": run.get("run_number"),
            "status": run.get("status"),
            "result": run.get("conclusion"),
            "definition_name": run.get("name") or run.get("display_title"),
            "repository_name": repo,
            "branch": run.get("head_branch"),
            "commit": run.get("head_sha"),
        }
        return {"metadata": metadata, "logs": jobs}

    def get_build_log_content(
        self: Any,
        *,
        repo: str,
        log_id: int,
    ) -> str:
        return self._request_text(
            "GET",
            f"{self._repo_prefix(repo)}/actions/jobs/{log_id}/logs",
            headers={"Accept": "application/vnd.github+json"},
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
        reverse: bool = False,
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
            reverse=reverse,
        )

    def list_pipelines(
        self: Any,
        *,
        repo: str,
        pipeline_id: int,
        query: PipelineListQuery,
    ) -> dict[str, Any]:
        run = self._request_json(
            "GET", f"{self._repo_prefix(repo)}/actions/runs/{pipeline_id}"
        )
        jobs = self._list_github_run_jobs(repo=repo, run_id=pipeline_id)
        row = build_pipeline_row(
            pipeline_id=run.get("id") or pipeline_id,
            iid=run.get("run_number"),
            project=repo,
            status=normalize_github_status(run.get("status"), run.get("conclusion")),
            ref=run.get("head_branch"),
            sha=run.get("head_sha"),
            name=run.get("name") or run.get("display_title"),
            source=run.get("event"),
            trigger_job=None,
            parent_id=None,
            depth=0,
            created_at=run.get("created_at") or run.get("run_started_at"),
            duration_s=None,
            url=run.get("html_url") or run.get("url"),
            jobs=jobs,
        )
        return build_pipeline_list_payload(rows=[row], query=query)

    def _list_github_run_jobs(
        self: Any, *, repo: str, run_id: int
    ) -> list[JobRow]:
        collected: list[JobRow] = []
        page = 1
        per_page = 100
        while True:
            try:
                payload = self._request_json(
                    "GET",
                    f"{self._repo_prefix(repo)}/actions/runs/{run_id}/jobs",
                    params={"per_page": per_page, "page": page},
                )
            except Exception as exc:  # noqa: BLE001 - jobs are best-effort metadata
                logger.info(
                    "GitHub jobs fetch failed for run %s in %s: %s",
                    run_id,
                    repo,
                    exc,
                )
                break

            if not isinstance(payload, dict):
                break

            batch = payload.get("jobs")
            if not isinstance(batch, list) or not batch:
                break

            for item in batch:
                if not isinstance(item, dict):
                    continue
                collected.append(_github_job_row(item))

            total = payload.get("total_count")
            if isinstance(total, int) and len(collected) >= total:
                break
            if len(batch) < per_page:
                break
            page += 1

        return collected
