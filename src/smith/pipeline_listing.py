from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from smith.utils import compile_search_pattern

DEFAULT_PIPELINE_LIST_TAKE = 20
MAX_PIPELINE_LIST_TAKE = 500

SHORT_STATUS: dict[str, str] = {
    "success": "ok",
    "failed": "err",
    "running": "run",
    "skipped": "skip",
    "manual": "man",
    "canceled": "can",
    "pending": "pen",
    "created": "cre",
    "scheduled": "sch",
    "waiting_for_resource": "wait",
    "preparing": "prep",
}


def short_status(status: Any) -> str:
    text = str(status or "").strip().lower()
    if not text:
        return "-"
    mapped = SHORT_STATUS.get(text)
    if mapped is not None:
        return mapped
    return text[:4] if len(text) > 4 else text


_MATRIX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(.*?)\s*\[\s*(\d+)\s*/\s*(\d+)\s*\]$"),
    re.compile(r"^(.*?)\s*\(\s*(\d+)\s*/\s*(\d+)\s*\)$"),
    re.compile(r"^(.*?)\s+(\d+)/(\d+)$"),
)


def parse_matrix_from_name(name: Any) -> tuple[str, tuple[int, int] | None]:
    clean = str(name or "").strip()
    if not clean:
        return clean, None
    for pattern in _MATRIX_PATTERNS:
        match = pattern.match(clean)
        if not match:
            continue
        base = match.group(1).strip()
        try:
            index = int(match.group(2))
            total = int(match.group(3))
        except ValueError:
            continue
        if index < 1 or total < 1 or index > total:
            continue
        if not base:
            continue
        return base, (index, total)
    return clean, None

VALID_PIPELINE_STATUSES: frozenset[str] = frozenset(
    {
        "created",
        "waiting_for_resource",
        "preparing",
        "pending",
        "running",
        "success",
        "failed",
        "canceled",
        "skipped",
        "manual",
        "scheduled",
    }
)


_GITLAB_STATUS_ALIASES: dict[str, str] = {
    "cancelled": "canceled",
}

_GITHUB_STATUS_MAP: dict[str, str] = {
    "queued": "pending",
    "in_progress": "running",
    "waiting": "pending",
    "requested": "pending",
    "pending": "pending",
}

_GITHUB_CONCLUSION_MAP: dict[str, str] = {
    "success": "success",
    "failure": "failed",
    "cancelled": "canceled",
    "timed_out": "failed",
    "action_required": "manual",
    "neutral": "success",
    "skipped": "skipped",
    "stale": "skipped",
    "startup_failure": "failed",
}

_AZDO_STATUS_MAP: dict[str, str] = {
    "none": "created",
    "notstarted": "pending",
    "postponed": "scheduled",
    "inprogress": "running",
    "cancelling": "canceled",
    "completed": "success",
}

_AZDO_RESULT_MAP: dict[str, str] = {
    "succeeded": "success",
    "succeededwithissues": "success",
    "partiallysucceeded": "failed",
    "failed": "failed",
    "canceled": "canceled",
    "cancelled": "canceled",
    "abandoned": "canceled",
    "skipped": "skipped",
}


def normalize_gitlab_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _GITLAB_STATUS_ALIASES.get(text, text)


def normalize_github_status(status: Any, conclusion: Any) -> str:
    status_text = str(status or "").strip().lower()
    conclusion_text = str(conclusion or "").strip().lower()
    if status_text == "completed":
        return _GITHUB_CONCLUSION_MAP.get(conclusion_text, "success")
    return _GITHUB_STATUS_MAP.get(status_text, status_text)


def normalize_azdo_status(status: Any, result: Any) -> str:
    status_text = str(status or "").strip().lower()
    result_text = str(result or "").strip().lower()
    if status_text == "completed" and result_text:
        return _AZDO_RESULT_MAP.get(result_text, result_text)
    return _AZDO_STATUS_MAP.get(status_text, status_text)


def _parse_duration_seconds(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _normalize_id(value: Any) -> Any:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


@dataclass(frozen=True)
class DownstreamRef:
    project: str
    pipeline_id: int | str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "pipeline_id": self.pipeline_id,
            "status": str(self.status or "").strip().lower() or None,
        }


@dataclass(frozen=True)
class JobRow:
    id: int | str
    name: str
    status: str
    stage: str | None = None
    duration_s: int | None = None
    matrix: tuple[int, int] | None = None
    allow_failure: bool = False
    manual: bool = False
    environment: str | None = None
    needs: tuple[str, ...] = ()
    downstream: DownstreamRef | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": str(self.status or "").strip().lower() or None,
            "stage": self.stage,
            "duration_s": self.duration_s,
            "matrix": list(self.matrix) if self.matrix is not None else None,
            "allow_failure": self.allow_failure,
            "manual": self.manual,
            "environment": self.environment,
            "needs": list(self.needs),
            "downstream": self.downstream.to_dict() if self.downstream is not None else None,
        }


def _normalize_job_parts(
    *,
    job_id: Any,
    name: str | None,
    status: str | None,
    stage: str | None = None,
    duration_s: Any = None,
    matrix: tuple[int, int] | None = None,
    allow_failure: bool = False,
    manual: bool = False,
    environment: str | None = None,
    needs: tuple[str, ...] | list[str] | None = None,
    downstream: DownstreamRef | None = None,
) -> tuple[
    Any,
    str,
    str,
    str | None,
    int | None,
    tuple[int, int] | None,
    bool,
    bool,
    str | None,
    tuple[str, ...],
    DownstreamRef | None,
]:
    base_name, parsed_matrix = parse_matrix_from_name(name)
    effective_matrix = matrix if matrix is not None else parsed_matrix
    normalized_needs = tuple(
        str(item).strip() for item in (needs or ()) if str(item).strip()
    )
    return (
        _normalize_id(job_id),
        base_name,
        str(status or "").strip().lower(),
        str(stage).strip() if stage not in (None, "") else None,
        _parse_duration_seconds(duration_s),
        effective_matrix,
        bool(allow_failure),
        bool(manual),
        str(environment).strip() if environment not in (None, "") else None,
        normalized_needs,
        downstream,
    )


def build_job_row(
    *,
    job_id: Any,
    name: str | None,
    status: str | None,
    stage: str | None = None,
    duration_s: Any = None,
    matrix: tuple[int, int] | None = None,
    allow_failure: bool = False,
    manual: bool = False,
    environment: str | None = None,
    needs: tuple[str, ...] | list[str] | None = None,
    downstream: DownstreamRef | None = None,
) -> JobRow:
    (
        normalized_id,
        base_name,
        normalized_status,
        normalized_stage,
        normalized_duration_s,
        effective_matrix,
        normalized_allow_failure,
        normalized_manual,
        normalized_environment,
        normalized_needs,
        normalized_downstream,
    ) = _normalize_job_parts(
        job_id=job_id,
        name=name,
        status=status,
        stage=stage,
        duration_s=duration_s,
        matrix=matrix,
        allow_failure=allow_failure,
        manual=manual,
        environment=environment,
        needs=needs,
        downstream=downstream,
    )
    return JobRow(
        id=normalized_id,
        name=base_name,
        status=normalized_status,
        stage=normalized_stage,
        duration_s=normalized_duration_s,
        matrix=effective_matrix,
        allow_failure=normalized_allow_failure,
        manual=normalized_manual,
        environment=normalized_environment,
        needs=normalized_needs,
        downstream=normalized_downstream,
    )


def build_job_dict(
    *,
    job_id: Any,
    name: str | None,
    status: str | None,
    stage: str | None = None,
    duration_s: Any = None,
    matrix: tuple[int, int] | None = None,
    allow_failure: bool = False,
    manual: bool = False,
    environment: str | None = None,
    needs: tuple[str, ...] | list[str] | None = None,
    downstream: DownstreamRef | None = None,
) -> dict[str, Any]:
    (
        normalized_id,
        base_name,
        normalized_status,
        normalized_stage,
        normalized_duration_s,
        effective_matrix,
        normalized_allow_failure,
        normalized_manual,
        normalized_environment,
        normalized_needs,
        normalized_downstream,
    ) = _normalize_job_parts(
        job_id=job_id,
        name=name,
        status=status,
        stage=stage,
        duration_s=duration_s,
        matrix=matrix,
        allow_failure=allow_failure,
        manual=manual,
        environment=environment,
        needs=needs,
        downstream=downstream,
    )
    return {
        "id": normalized_id,
        "name": base_name,
        "status": normalized_status or None,
        "stage": normalized_stage,
        "duration_s": normalized_duration_s,
        "matrix": list(effective_matrix) if effective_matrix is not None else None,
        "allow_failure": normalized_allow_failure,
        "manual": normalized_manual,
        "environment": normalized_environment,
        "needs": list(normalized_needs),
        "downstream": normalized_downstream.to_dict()
        if normalized_downstream is not None
        else None,
    }


@dataclass(frozen=True)
class PipelineListQuery:
    grep: str | None = None
    statuses: tuple[str, ...] = ()
    skip: int = 0
    take: int = DEFAULT_PIPELINE_LIST_TAKE
    requested_take: int = DEFAULT_PIPELINE_LIST_TAKE
    max_depth: int = 0
    warnings: tuple[str, ...] = ()
    _compiled_pattern: re.Pattern[str] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    @classmethod
    def create(
        cls,
        *,
        grep: str | None = None,
        statuses: list[str] | None = None,
        skip: int | None = None,
        take: int | None = None,
        max_depth: int | None = None,
    ) -> "PipelineListQuery":
        normalized_grep = str(grep or "").strip() or None

        normalized_statuses: tuple[str, ...] = ()
        if statuses:
            collected: list[str] = []
            for raw in statuses:
                text = str(raw or "").strip().lower()
                if not text:
                    continue
                if text not in VALID_PIPELINE_STATUSES:
                    valid = ", ".join(sorted(VALID_PIPELINE_STATUSES))
                    raise ValueError(f"status must be one of: {valid}")
                if text not in collected:
                    collected.append(text)
            normalized_statuses = tuple(collected)

        normalized_skip = max(0, int(skip or 0))
        requested_take = int(take if take is not None else DEFAULT_PIPELINE_LIST_TAKE)
        normalized_take = min(MAX_PIPELINE_LIST_TAKE, max(1, requested_take))

        warnings_list: list[str] = []
        if requested_take > MAX_PIPELINE_LIST_TAKE:
            warnings_list.append(f"`--take` capped at {MAX_PIPELINE_LIST_TAKE}.")

        normalized_max_depth = max(0, int(max_depth or 0))

        instance = cls(
            grep=normalized_grep,
            statuses=normalized_statuses,
            skip=normalized_skip,
            take=normalized_take,
            requested_take=requested_take,
            max_depth=normalized_max_depth,
            warnings=tuple(warnings_list),
        )
        if normalized_grep:
            compiled, error = compile_search_pattern(normalized_grep, case_insensitive=True)
            if compiled is None:
                raise ValueError(error or "Error: Invalid regex pattern")
            object.__setattr__(instance, "_compiled_pattern", compiled)
        return instance

    def allows_depth(self, depth: int) -> bool:
        if self.max_depth <= 0:
            return True
        return depth < self.max_depth

    def row_matches(self, row: dict[str, Any]) -> bool:
        if self.statuses and str(row.get("status") or "").strip().lower() not in self.statuses:
            return False
        if self._compiled_pattern is not None:
            haystack = " ".join(
                str(row.get(key) or "").strip()
                for key in ("project", "name", "ref", "status", "source")
            )
            if not self._compiled_pattern.search(haystack):
                return False
        return True


def build_pipeline_row(
    *,
    pipeline_id: Any,
    project: str | None,
    status: str | None,
    ref: str | None = None,
    sha: str | None = None,
    name: str | None = None,
    source: str | None = None,
    trigger_job: str | None = None,
    trigger_stage: str | None = None,
    parent_id: int | None = None,
    depth: int = 0,
    created_at: str | None = None,
    duration_s: Any = None,
    url: str | None = None,
    iid: int | None = None,
    project_id: Any = None,
    jobs: list[JobRow] | list[dict[str, Any]] | None = None,
    copy_jobs: bool = True,
) -> dict[str, Any]:
    normalized_id: Any = pipeline_id
    try:
        normalized_id = int(pipeline_id)
    except (TypeError, ValueError):
        normalized_id = pipeline_id
    row: dict[str, Any] = {
        "id": normalized_id,
        "iid": iid,
        "project": str(project or "").strip() or None,
        "project_id": project_id,
        "status": str(status or "").strip() or None,
        "ref": str(ref or "").strip() or None,
        "sha": str(sha or "").strip() or None,
        "name": str(name or "").strip() or None,
        "source": str(source or "").strip() or None,
        "trigger_job": str(trigger_job or "").strip() or None,
        "parent_id": parent_id,
        "depth": int(depth),
        "created_at": str(created_at or "").strip() or None,
        "duration_s": _parse_duration_seconds(duration_s),
        "url": str(url or "").strip() or None,
    }
    if trigger_stage not in (None, ""):
        row["trigger_stage"] = str(trigger_stage).strip() or None
    if jobs is not None:
        serialized_jobs: list[dict[str, Any]] = []
        for job in jobs:
            if isinstance(job, JobRow):
                serialized_jobs.append(job.to_dict())
                continue
            if copy_jobs or not isinstance(job, dict):
                serialized_jobs.append(dict(job))
                continue
            serialized_jobs.append(job)
        row["jobs"] = serialized_jobs
    return row


def _pipeline_identity_key(row: dict[str, Any]) -> tuple[str, Any]:
    project = str(row.get("project") or row.get("project_id") or "").strip().lower()
    return project, _normalize_id(row.get("id"))


def _downstream_identity_key(downstream: Any) -> tuple[str, Any] | None:
    if not isinstance(downstream, dict):
        return None
    project = str(downstream.get("project") or "").strip().lower()
    pipeline_id = _normalize_id(downstream.get("pipeline_id"))
    if not project or pipeline_id in (None, ""):
        return None
    return project, pipeline_id


def _job_matches_statuses(job: dict[str, Any], statuses: tuple[str, ...]) -> bool:
    if not statuses:
        return False
    return str(job.get("status") or "").strip().lower() in statuses


def _resolve_needed_job_indexes(
    need: Any,
    *,
    indexes_by_id: dict[Any, set[int]],
    indexes_by_name: dict[str, set[int]],
) -> set[int]:
    if need is None:
        return set()
    if isinstance(need, (int, float)) and not isinstance(need, bool):
        return set(indexes_by_id.get(_normalize_id(need), set()))

    text = str(need or "").strip()
    if not text:
        return set()

    by_name = indexes_by_name.get(text)
    if by_name:
        return set(by_name)

    direct_id = indexes_by_id.get(_normalize_id(text))
    if direct_id:
        return set(direct_id)

    if text.startswith("j"):
        prefixed_id = indexes_by_id.get(_normalize_id(text[1:]))
        if prefixed_id:
            return set(prefixed_id)

    return set()


def _filter_jobs_for_statuses(
    jobs: list[dict[str, Any]],
    *,
    statuses: tuple[str, ...],
    selected_pipeline_keys: set[tuple[str, Any]],
) -> list[dict[str, Any]]:
    if not statuses:
        return [dict(job) for job in jobs]

    indexes_by_id: dict[Any, set[int]] = {}
    indexes_by_name: dict[str, set[int]] = {}
    for index, job in enumerate(jobs):
        job_id = _normalize_id(job.get("id"))
        if job_id not in (None, ""):
            indexes_by_id.setdefault(job_id, set()).add(index)

        name = str(job.get("name") or "").strip()
        if name:
            indexes_by_name.setdefault(name, set()).add(index)

    selected_indexes = set()
    for index, job in enumerate(jobs):
        if _job_matches_statuses(job, statuses):
            selected_indexes.add(index)
            continue
        downstream_key = _downstream_identity_key(job.get("downstream"))
        if downstream_key is not None and downstream_key in selected_pipeline_keys:
            selected_indexes.add(index)

    stack = list(selected_indexes)
    while stack:
        job = jobs[stack.pop()]
        needs = job.get("needs")
        if not isinstance(needs, (list, tuple)):
            continue
        for need in needs:
            for needed_index in _resolve_needed_job_indexes(
                need,
                indexes_by_id=indexes_by_id,
                indexes_by_name=indexes_by_name,
            ):
                if needed_index in selected_indexes:
                    continue
                selected_indexes.add(needed_index)
                stack.append(needed_index)

    return [dict(job) for index, job in enumerate(jobs) if index in selected_indexes]


def _slice_pipeline_jobs_for_query(
    row: dict[str, Any],
    *,
    query: PipelineListQuery,
    selected_pipeline_keys: set[tuple[str, Any]],
) -> dict[str, Any]:
    raw_jobs = row.get("jobs")
    if not isinstance(raw_jobs, list):
        return row

    jobs = [job for job in raw_jobs if isinstance(job, dict)]
    sliced = _filter_jobs_for_statuses(
        jobs,
        statuses=query.statuses,
        selected_pipeline_keys=selected_pipeline_keys,
    )
    updated = dict(row)
    updated["jobs"] = sliced
    return updated


def _paginate(rows: list[dict[str, Any]], *, skip: int, take: int) -> list[dict[str, Any]]:
    start = max(0, skip)
    return rows[start : start + max(1, take)]


def build_pipeline_list_payload(
    *,
    rows: list[dict[str, Any]],
    query: PipelineListQuery,
    extra_warnings: list[str] | None = None,
) -> dict[str, Any]:
    filtered = [row for row in rows if query.row_matches(row)]
    total_count = len(filtered)
    paged = _paginate(filtered, skip=query.skip, take=query.take)
    rendered_rows = paged
    if query.statuses:
        selected_pipeline_keys = {
            _pipeline_identity_key(row)
            for row in filtered
            if isinstance(row, dict)
        }
        rendered_rows = [
            _slice_pipeline_jobs_for_query(
                row,
                query=query,
                selected_pipeline_keys=selected_pipeline_keys,
            )
            for row in paged
        ]

    warnings: list[str] = list(query.warnings)
    if extra_warnings:
        warnings.extend(extra_warnings)
    if total_count > query.skip + len(paged):
        warnings.append(
            f"showing {len(paged)} of {total_count} matching pipelines; use --skip/--take to see more."
        )

    return {
        "pipelines": rendered_rows,
        "returned_count": len(paged),
        "total_count": total_count,
        "warnings": warnings,
        "partial": bool(warnings),
    }
