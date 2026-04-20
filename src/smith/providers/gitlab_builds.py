from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Literal

from smith.errors import SmithApiError
from smith.pipeline_listing import (
    DownstreamRef,
    PipelineListQuery,
    build_job_dict,
    build_pipeline_list_payload,
    build_pipeline_row,
    normalize_gitlab_status,
)
from smith.providers.helpers import grep_build_logs_core

if TYPE_CHECKING:  # pragma: no cover - import-time only
    pass


logger = logging.getLogger(__name__)

_BRIDGE_MAX_WORKERS = 8

_PIPELINE_GQL_QUERY_RICH = """
query($fullPath: ID!, $id: CiPipelineID!) {
  project(fullPath: $fullPath) {
    pipeline(id: $id) {
      id
      iid
      name
      ref
      status
      sha
      commit { title }
      duration
      createdAt
      webUrl
      path
      project { id fullPath }
      stages(first: 100) {
        nodes {
          name
          groups(first: 200) {
            nodes {
              jobs(first: 500) {
                nodes {
                  id
                  name
                  status
                  duration
                  allowFailure
                  manualJob
                  environment { name }
                  previousStageJobsOrNeeds {
                    nodes {
                      ... on CiBuildNeed { name }
                      ... on CiJob { name }
                    }
                  }
                  downstreamPipeline {
                    id
                    iid
                    status
                    project { id fullPath }
                  }
                }
              }
            }
          }
        }
      }
      downstream(first: 200) {
        nodes {
          id
          iid
          status
          project { id fullPath }
          sourceJob {
            name
            stage { name }
          }
        }
      }
    }
  }
}
"""

_PIPELINE_GQL_QUERY_COMPAT = """
query($fullPath: ID!, $id: CiPipelineID!) {
  project(fullPath: $fullPath) {
    pipeline(id: $id) {
      id
      iid
      name
      ref
      status
      sha
      commit { title }
      duration
      createdAt
      path
      project { id fullPath }
      stages(first: 100) {
        nodes {
          name
          groups(first: 200) {
            nodes {
              jobs(first: 500) {
                nodes {
                  id
                  name
                  status
                  duration
                  allowFailure
                  manualJob
                  previousStageJobsOrNeeds: previousStageJobs(first: 200) {
                    nodes {
                      name
                    }
                  }
                  downstreamPipeline {
                    id
                    iid
                    status
                    project { id fullPath }
                  }
                }
              }
            }
          }
        }
      }
      downstream(first: 200) {
        nodes {
          id
          iid
          status
          project { id fullPath }
          sourceJob {
            name
            stage { name }
          }
        }
      }
    }
  }
}
"""

def _pipeline_query_by_iid(query_text: str) -> str:
    return (
        query_text.replace("$id: CiPipelineID!", "$iid: ID!", 1).replace(
            "pipeline(id: $id)", "pipeline(iid: $iid)", 1
        )
    )


_PIPELINE_GQL_QUERY_RICH_BY_IID = _pipeline_query_by_iid(_PIPELINE_GQL_QUERY_RICH)
_PIPELINE_GQL_QUERY_COMPAT_BY_IID = _pipeline_query_by_iid(_PIPELINE_GQL_QUERY_COMPAT)

_PIPELINE_GQL_VARIANTS: tuple[tuple[str, str], ...] = (
    ("rich", _PIPELINE_GQL_QUERY_RICH),
    ("compat", _PIPELINE_GQL_QUERY_COMPAT),
)

_PIPELINE_GQL_QUERY_MAP: dict[str, dict[str, str]] = {
    "rich": {
        "id": _PIPELINE_GQL_QUERY_RICH,
        "iid": _PIPELINE_GQL_QUERY_RICH_BY_IID,
    },
    "compat": {
        "id": _PIPELINE_GQL_QUERY_COMPAT,
        "iid": _PIPELINE_GQL_QUERY_COMPAT_BY_IID,
    },
}

_GID_PIPELINE_PATTERN = re.compile(r"/(?:Ci::Pipeline|CiPipeline)/(\d+)$")
_GID_JOB_PATTERN = re.compile(r"/(?:Ci::Build|Ci::Bridge|CiJob)/(\d+)$")
_GID_PROJECT_PATTERN = re.compile(r"/Project/(\d+)$")


def _extract_numeric_id(gid: Any, pattern: re.Pattern[str]) -> int | str | None:
    if gid is None:
        return None
    if isinstance(gid, int):
        return gid
    text = str(gid).strip()
    if not text:
        return None
    match = pattern.search(text)
    if match:
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return text
    try:
        return int(text)
    except (TypeError, ValueError):
        return text


def _pipeline_gid(pipeline_id: int | str) -> str:
    return f"gid://gitlab/Ci::Pipeline/{pipeline_id}"


def _is_graphql_schema_error(exc: SmithApiError) -> bool:
    message = str(exc)
    markers = (
        "doesn't exist on type",
        "Cannot query field",
        "Unknown argument",
        "Unknown type",
        "Variable $",
    )
    return any(marker in message for marker in markers)


def _absolute_gitlab_url(base_url: str, path: Any) -> str | None:
    text = str(path or "").strip()
    if not text:
        return None
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return f"{base_url.rstrip('/')}/{text.lstrip('/')}"


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
        try:
            return self._list_pipelines_graphql(
                repo=repo, pipeline_id=pipeline_id, query=query
            )
        except Exception as exc:  # noqa: BLE001 - intentional broad fallback guard
            logger.info(
                "GitLab GraphQL pipeline traversal failed, falling back to REST: %s",
                exc,
            )
            payload = self._list_pipelines_rest(
                repo=repo, pipeline_id=pipeline_id, query=query
            )
            fallback_warning = (
                "GitLab GraphQL unavailable; using REST fallback with limited per-job metadata."
            )
            existing = list(payload.get("warnings") or [])
            if fallback_warning not in existing:
                existing.append(fallback_warning)
            payload["warnings"] = existing
            payload["partial"] = bool(existing)
            return payload

    def _graphql_pipeline_node(
        self: Any,
        *,
        pipeline_gid: str,
        pipeline_iid: str | None,
        project_path: str,
        prefer_iid: bool = False,
    ) -> dict[str, Any] | None:
        variant_attr = "_gitlab_pipeline_graphql_variant"

        def _run_query(query_text: str, *, variables: dict[str, Any]) -> dict[str, Any] | None:
            data = self._graphql(
                query_text,
                variables,
            )
            if isinstance(data, dict):
                direct_node = data.get("ciPipeline")
                if isinstance(direct_node, dict):
                    return direct_node
            project = data.get("project") if isinstance(data, dict) else None
            if not isinstance(project, dict):
                return None
            node = project.get("pipeline")
            return node if isinstance(node, dict) else None

        lookup_order: list[tuple[str, str]] = []
        normalized_iid = str(pipeline_iid or "").strip()
        if prefer_iid and normalized_iid:
            lookup_order.append(("iid", normalized_iid))
        if pipeline_gid:
            lookup_order.append(("id", pipeline_gid))
        if normalized_iid and not prefer_iid:
            lookup_order.append(("iid", normalized_iid))

        def _run_variant(variant_name: str) -> dict[str, Any] | None:
            for locator_kind, locator_value in lookup_order:
                node = _run_query(
                    _PIPELINE_GQL_QUERY_MAP[variant_name][locator_kind],
                    variables={
                        "fullPath": project_path,
                        locator_kind: locator_value,
                    },
                )
                if isinstance(node, dict):
                    return node
            return None

        cached_variant = getattr(self, variant_attr, None)
        if cached_variant:
            try:
                return _run_variant(cached_variant)
            except SmithApiError as exc:
                if cached_variant != "rich" or not _is_graphql_schema_error(exc):
                    raise
                node = _run_variant("compat")
                setattr(self, variant_attr, "compat")
                return node

        last_error: SmithApiError | None = None
        for variant_name, _query_text in _PIPELINE_GQL_VARIANTS:
            try:
                node = _run_variant(variant_name)
            except SmithApiError as exc:
                last_error = exc
                if not _is_graphql_schema_error(exc):
                    raise
                continue
            setattr(self, variant_attr, variant_name)
            return node

        if last_error is not None:
            raise last_error
        return None

    def _list_pipelines_graphql(
        self: Any,
        *,
        repo: str,
        pipeline_id: int,
        query: PipelineListQuery,
    ) -> dict[str, Any]:
        root_gid = _pipeline_gid(pipeline_id)
        rows: list[dict[str, Any]] = []
        warnings: list[str] = []
        seen: set[tuple[Any, Any]] = set()
        max_depth_hit = False

        current_level: list[tuple[str, str | None, int | None, str | None, str | None, str | None]] = [
            (root_gid, repo, None, None, None, None)
        ]
        depth = 0
        while current_level:
            if not query.allows_depth(depth):
                max_depth_hit = True
                break

            resolved = self._resolve_graphql_level(
                current_level=current_level,
                depth=depth,
                seen=seen,
                repo_hint=repo,
            )
            if not resolved:
                break

            next_level: list[tuple[str, str | None, int | None, str | None, str | None, str | None]] = []
            for row, downstream_refs in resolved:
                rows.append(row)
                for child_gid, child_project_path, trigger_job, child_iid, trigger_stage in downstream_refs:
                    next_level.append(
                        (
                            child_gid,
                            child_project_path,
                            row.get("id"),
                            trigger_job,
                            child_iid,
                            trigger_stage,
                        )
                    )

            current_level = next_level
            depth += 1

        if max_depth_hit and query.max_depth > 0:
            warnings.append(
                f"max depth {query.max_depth} reached; deeper downstream pipelines not traversed."
            )

        return build_pipeline_list_payload(rows=rows, query=query, extra_warnings=warnings)

    def _resolve_graphql_level(
        self: Any,
        *,
        current_level: list[tuple[str, str | None, int | None, str | None, str | None, str | None]],
        depth: int,
        seen: set[tuple[Any, Any]],
        repo_hint: str,
    ) -> list[tuple[dict[str, Any], list[tuple[str, str | None, str | None, str | None, str | None]]]]:
        def _fetch(
            entry: tuple[str, str | None, int | None, str | None, str | None, str | None]
        ) -> tuple[dict[str, Any], list[tuple[str, str | None, str | None, str | None, str | None]]] | None:
            gid, project_path, parent_id, trigger_job, pipeline_iid, trigger_stage = entry
            node = self._graphql_pipeline_node(
                pipeline_gid=gid,
                pipeline_iid=pipeline_iid,
                project_path=str(project_path or "").strip() or repo_hint,
                prefer_iid=pipeline_iid is not None and parent_id is not None,
            )
            if not isinstance(node, dict):
                return None
            row, child_refs = _build_row_from_graphql(
                node=node,
                parent_id=parent_id,
                trigger_job=trigger_job,
                trigger_stage=trigger_stage,
                depth=depth,
                repo_hint=repo_hint,
            )
            row["url"] = _absolute_gitlab_url(
                self._gitlab_web_url(),
                row.get("url") or node.get("path"),
            )
            project_path = row.get("project")
            if project_path:
                self._cache_project(
                    project_id=str(row.get("project_id") or "") or None,
                    full_path=str(project_path),
                )
            identity = (row.get("project_id"), row.get("id"))
            if identity in seen:
                return None
            seen.add(identity)
            return row, child_refs

        if len(current_level) <= 1:
            result = _fetch(current_level[0]) if current_level else None
            return [result] if result is not None else []

        max_workers = min(_BRIDGE_MAX_WORKERS, len(current_level))
        outputs: list[
            tuple[dict[str, Any], list[tuple[str, str | None, str | None, str | None, str | None]]] | None
        ] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_fetch, entry) for entry in current_level]
            for future in futures:
                outputs.append(future.result())
        return [item for item in outputs if item is not None]

    def _list_pipelines_rest(
        self: Any,
        *,
        repo: str,
        pipeline_id: int,
        query: PipelineListQuery,
    ) -> dict[str, Any]:
        root_project_path = self._full_project_path(repo)
        root_raw = self._request_json(
            "GET", f"/projects/{self._project_id(repo)}/pipelines/{pipeline_id}"
        )
        self._cache_project(
            project_id=str(root_raw.get("project_id") or "") or None,
            full_path=root_project_path,
        )
        root_row = _gitlab_pipeline_row(
            raw=root_raw,
            project_path=root_project_path,
            parent_id=None,
            trigger_job=None,
            trigger_stage=None,
            depth=0,
        )
        rows: list[dict[str, Any]] = [root_row]
        warnings: list[str] = []
        seen: set[tuple[Any, Any]] = {(root_row.get("project_id"), root_row.get("id"))}
        max_depth_hit = False

        current_level: list[dict[str, Any]] = [root_row]
        depth = 0
        while current_level:
            if not query.allows_depth(depth):
                max_depth_hit = True
                break
            next_level = self._fetch_next_downstream_level(
                parents=current_level,
                depth=depth + 1,
                seen=seen,
            )
            if not next_level:
                break
            rows.extend(next_level)
            current_level = next_level
            depth += 1

        if max_depth_hit and query.max_depth > 0:
            warnings.append(
                f"max depth {query.max_depth} reached; deeper downstream pipelines not traversed."
            )

        return build_pipeline_list_payload(rows=rows, query=query, extra_warnings=warnings)

    def _fetch_next_downstream_level(
        self: Any,
        *,
        parents: list[dict[str, Any]],
        depth: int,
        seen: set[tuple[Any, Any]],
    ) -> list[dict[str, Any]]:
        bridge_results = self._fetch_bridges_for_pipelines(parents)

        downstream_lookups: list[tuple[dict[str, Any], dict[str, Any], str | None, str | None]] = []
        for parent_row, bridges in bridge_results:
            for bridge in bridges:
                if not isinstance(bridge, dict):
                    continue
                downstream = bridge.get("downstream_pipeline")
                if not isinstance(downstream, dict):
                    continue
                downstream_id = downstream.get("id")
                if downstream_id is None:
                    continue
                downstream_project_id = downstream.get("project_id")
                identity = (downstream_project_id, downstream_id)
                if identity in seen:
                    continue
                seen.add(identity)
                downstream_lookups.append(
                    (
                        parent_row,
                        downstream,
                        str(bridge.get("name") or "").strip() or None,
                        str(bridge.get("stage") or "").strip() or None,
                    )
                )

        if not downstream_lookups:
            return []

        return self._resolve_downstream_rows(lookups=downstream_lookups, depth=depth)

    def _fetch_bridges_for_pipelines(
        self: Any,
        parents: list[dict[str, Any]],
    ) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
        def _fetch(parent_row: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            project_path = parent_row.get("project")
            pipeline_id = parent_row.get("id")
            if not project_path or pipeline_id is None:
                return parent_row, []
            try:
                bridges = self._get_paginated_list(
                    f"/projects/{self._project_id(str(project_path))}/pipelines/{pipeline_id}/bridges"
                )
            except Exception:
                bridges = []
            return parent_row, list(bridges) if isinstance(bridges, list) else []

        max_workers = min(_BRIDGE_MAX_WORKERS, max(1, len(parents)))
        if max_workers <= 1 or len(parents) <= 1:
            return [_fetch(parent_row) for parent_row in parents]

        results: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_fetch, parent_row) for parent_row in parents]
            for future in futures:
                results.append(future.result())
        return results

    def _resolve_downstream_rows(
        self: Any,
        *,
        lookups: list[tuple[dict[str, Any], dict[str, Any], str | None, str | None]],
        depth: int,
    ) -> list[dict[str, Any]]:
        project_id_counts: dict[str, int] = {}
        for _parent_row, downstream, _trigger_job, _trigger_stage in lookups:
            project_id_text = str(downstream.get("project_id") or "").strip()
            if not project_id_text:
                continue
            project_id_counts[project_id_text] = project_id_counts.get(project_id_text, 0) + 1

        shared_project_paths = {
            project_id: self._project_path_from_id(project_id)
            for project_id, count in project_id_counts.items()
            if count > 1
        }

        def _resolve(
            parent_row: dict[str, Any],
            downstream: dict[str, Any],
            trigger_job: str | None,
            trigger_stage: str | None,
        ) -> dict[str, Any] | None:
            project_id_text = str(downstream.get("project_id") or "").strip()
            if not project_id_text:
                return None
            project_path = shared_project_paths.get(project_id_text)
            if project_path is None:
                project_path = self._project_path_from_id(project_id_text)
            if not project_path:
                return None
            pipeline_id = downstream.get("id")
            try:
                full = self._request_json(
                    "GET",
                    f"/projects/{self._project_id(str(project_path))}/pipelines/{pipeline_id}",
                )
            except Exception:
                full = downstream
            return _gitlab_pipeline_row(
                raw=full,
                project_path=str(project_path),
                parent_id=parent_row.get("id"),
                trigger_job=trigger_job,
                trigger_stage=trigger_stage,
                depth=depth,
            )

        max_workers = min(_BRIDGE_MAX_WORKERS, max(1, len(lookups)))
        if max_workers <= 1 or len(lookups) <= 1:
            rows: list[dict[str, Any]] = []
            for parent_row, downstream, trigger_job, trigger_stage in lookups:
                row = _resolve(parent_row, downstream, trigger_job, trigger_stage)
                if row is not None:
                    rows.append(row)
            return rows

        rows = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(_resolve, parent_row, downstream, trigger_job, trigger_stage)
                for parent_row, downstream, trigger_job, trigger_stage in lookups
            ]
            for future in futures:
                row = future.result()
                if row is not None:
                    rows.append(row)
        return rows


def _gitlab_pipeline_row(
    *,
    raw: dict[str, Any],
    project_path: str,
    parent_id: int | None,
    trigger_job: str | None,
    trigger_stage: str | None,
    depth: int,
) -> dict[str, Any]:
    duration = raw.get("duration")
    if duration is None:
        duration = raw.get("queued_duration")
    return build_pipeline_row(
        pipeline_id=raw.get("id"),
        iid=raw.get("iid"),
        project=project_path,
        project_id=raw.get("project_id"),
        status=normalize_gitlab_status(raw.get("status")),
        ref=raw.get("ref"),
        sha=raw.get("sha"),
        name=raw.get("name"),
        source=raw.get("source"),
        trigger_job=trigger_job,
        trigger_stage=trigger_stage,
        parent_id=parent_id,
        depth=depth,
        created_at=raw.get("created_at"),
        duration_s=duration,
        url=raw.get("web_url"),
    )


def _graphql_node_list(container: Any) -> list[dict[str, Any]]:
    if not isinstance(container, dict):
        return []
    nodes = container.get("nodes")
    if not isinstance(nodes, list):
        return []
    return [node for node in nodes if isinstance(node, dict)]


def _graphql_needs(job: dict[str, Any]) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    for node in _graphql_node_list(job.get("previousStageJobsOrNeeds")):
        name = str(node.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return tuple(names)


def _graphql_downstream_ref(job: dict[str, Any]) -> DownstreamRef | None:
    downstream = job.get("downstreamPipeline")
    if not isinstance(downstream, dict):
        return None
    project = downstream.get("project") or {}
    project_path = str(project.get("fullPath") or "").strip()
    pipeline_id = _extract_numeric_id(downstream.get("id"), _GID_PIPELINE_PATTERN)
    if pipeline_id is None or not project_path:
        return None
    status = normalize_gitlab_status(downstream.get("status"))
    return DownstreamRef(project=project_path, pipeline_id=pipeline_id, status=status)


def _graphql_job_dict(job: dict[str, Any], *, stage: str | None) -> dict[str, Any]:
    raw_status = str(job.get("status") or "").strip()
    normalized_status = normalize_gitlab_status(raw_status) if raw_status else ""
    is_manual = bool(job.get("manualJob")) or normalized_status == "manual"
    environment_node = job.get("environment")
    environment_name: str | None = None
    if isinstance(environment_node, dict):
        environment_name = str(environment_node.get("name") or "").strip() or None
    return build_job_dict(
        job_id=_extract_numeric_id(job.get("id"), _GID_JOB_PATTERN),
        name=job.get("name"),
        status=normalized_status,
        stage=stage,
        duration_s=job.get("duration"),
        allow_failure=bool(job.get("allowFailure")),
        manual=is_manual,
        environment=environment_name,
        needs=_graphql_needs(job),
        downstream=_graphql_downstream_ref(job),
    )


def _build_row_from_graphql(
    *,
    node: dict[str, Any],
    parent_id: int | None,
    trigger_job: str | None,
    trigger_stage: str | None,
    depth: int,
    repo_hint: str,
) -> tuple[dict[str, Any], list[tuple[str, str | None, str | None, str | None, str | None]]]:
    project_info = node.get("project") or {}
    project_path = str(project_info.get("fullPath") or "").strip() or repo_hint
    project_id = _extract_numeric_id(project_info.get("id"), _GID_PROJECT_PATTERN)
    pipeline_id = _extract_numeric_id(node.get("id"), _GID_PIPELINE_PATTERN)
    iid_value = node.get("iid")
    iid_int: int | None
    try:
        iid_int = int(iid_value) if iid_value is not None else None
    except (TypeError, ValueError):
        iid_int = None

    job_rows: list[dict[str, Any]] = []
    child_refs: list[tuple[str, str | None, str | None, str | None, str | None]] = []
    for stage_node in _graphql_node_list(node.get("stages")):
        stage_name = str(stage_node.get("name") or "").strip() or None
        for group_node in _graphql_node_list(stage_node.get("groups")):
            for job_node in _graphql_node_list(group_node.get("jobs")):
                job_row = _graphql_job_dict(job_node, stage=stage_name)
                job_rows.append(job_row)
                downstream = job_row.get("downstream")
                if isinstance(downstream, dict):
                    downstream_node = job_node.get("downstreamPipeline") or {}
                    child_gid = str(downstream_node.get("id") or "").strip()
                    child_iid = str(downstream_node.get("iid") or "").strip() or None
                    if child_gid:
                        child_refs.append(
                            (
                                child_gid,
                                str(downstream.get("project") or "").strip() or None,
                                str(job_row.get("name") or "").strip() or None,
                                child_iid,
                                str(job_row.get("stage") or "").strip() or None,
                            )
                        )

    known_downstream_ids: set[Any] = set()
    for job in job_rows:
        downstream = job.get("downstream")
        if not isinstance(downstream, dict):
            continue
        known_downstream_ids.add(downstream.get("pipeline_id"))
    for downstream_node in _graphql_node_list(node.get("downstream")):
        downstream_id_raw = downstream_node.get("id")
        downstream_pid = _extract_numeric_id(downstream_id_raw, _GID_PIPELINE_PATTERN)
        if downstream_pid in known_downstream_ids:
            continue
        gid_text = str(downstream_id_raw or "").strip()
        if gid_text:
            downstream_project = downstream_node.get("project") or {}
            downstream_iid = str(downstream_node.get("iid") or "").strip() or None
            source_job = downstream_node.get("sourceJob") or {}
            source_stage = source_job.get("stage") if isinstance(source_job, dict) else None
            child_refs.append(
                (
                    gid_text,
                    str(downstream_project.get("fullPath") or "").strip() or None,
                    str(source_job.get("name") or "").strip() or None
                    if isinstance(source_job, dict)
                    else None,
                    downstream_iid,
                    str(source_stage.get("name") or "").strip() or None
                    if isinstance(source_stage, dict)
                    else None,
                )
            )

    commit_info = node.get("commit") or {}
    pipeline_name = str(node.get("name") or "").strip() or None
    if pipeline_name is None and isinstance(commit_info, dict):
        pipeline_name = str(commit_info.get("title") or "").strip() or None

    row = build_pipeline_row(
        pipeline_id=pipeline_id,
        iid=iid_int,
        project=project_path,
        project_id=project_id,
        status=normalize_gitlab_status(node.get("status")),
        ref=node.get("ref"),
        sha=node.get("sha"),
        name=pipeline_name,
        source=None,
        trigger_job=trigger_job,
        trigger_stage=trigger_stage,
        parent_id=parent_id,
        depth=depth,
        created_at=node.get("createdAt"),
        duration_s=node.get("duration"),
        url=node.get("webUrl") or node.get("path"),
        jobs=job_rows,
        copy_jobs=False,
    )
    return row, child_refs
