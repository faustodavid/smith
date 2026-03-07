from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from smith.providers.helpers import grep_build_logs_core

if TYPE_CHECKING:
    pass


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
