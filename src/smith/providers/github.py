from __future__ import annotations

import base64
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import quote

import requests

from smith.config import RuntimeConfig
from smith.errors import SmithApiError, SmithAuthError
from smith.formatting import format_grep_matches, glob_to_regex, normalize_branch_name, truncate_output
from smith.providers.base import BaseProvider
from smith.config import parse_bool_env, parse_int_env
from smith.utils import (
    compile_search_pattern,
    match_all_pattern,
    normalize_path,
    parse_iso_datetime,
    slice_lines,
)

GITHUB_DEFAULT_API_URL = "https://api.github.com"
GITHUB_DEFAULT_API_VERSION = "2022-11-28"


class GitHubProvider(BaseProvider):
    def __init__(self, *, config: RuntimeConfig, session: requests.Session) -> None:
        super().__init__(config=config, session=session)
        self.github_org = config.github_org
        self.github_api_url = config.github_api_url
        self.github_api_version = config.github_api_version
        self.max_output_chars = config.max_output_chars
        self._github_token: str | None = None
        self._default_branch_cache: dict[str, str] = {}

    def _get_token(self, *, force_refresh: bool = False) -> str:
        if self._github_token and not force_refresh:
            return self._github_token

        env_token = os.getenv("GITHUB_TOKEN", "").strip()
        if env_token:
            self._github_token = env_token
            return self._github_token

        try:
            result = subprocess.run(
                ["gh", "auth", "token"],
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception as exc:
            raise SmithAuthError(
                "Failed to acquire GitHub token. Set GITHUB_TOKEN or run `gh auth login`."
            ) from exc

        token = result.stdout.strip()
        if not token:
            raise SmithAuthError(
                "GitHub token is empty. Set GITHUB_TOKEN or run `gh auth login`."
            )

        self._github_token = token
        return self._github_token

    def _auth_error_message(self) -> str:
        return (
            "GitHub authentication rejected with HTTP 401/403. "
            "Set GITHUB_TOKEN or run `gh auth login` and retry."
        )

    def _default_accept_header(self) -> str:
        return "application/vnd.github+json"

    def _default_headers(self) -> dict[str, str]:
        return {"X-GitHub-Api-Version": self.github_api_version}

    def _timeout(self) -> int:
        return self._config.github_timeout_seconds

    def _build_url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.github_api_url}{path}"

    def _handle_response_status(self, response: Any, resolved_url: str) -> None:
        if response.status_code == 429:
            raise SmithApiError(
                "GitHub API rate limited (HTTP 429). Narrow scope and retry.",
                status_code=response.status_code,
            )

    def _require_github_org(self) -> str:
        org = (self.github_org or "").strip()
        if not org:
            raise ValueError("Missing GITHUB_ORG. Example: export GITHUB_ORG=<org>")
        return org

    def _repo_prefix(self, repo: str) -> str:
        org = self._require_github_org()
        return f"/repos/{quote(org, safe='')}/{quote(repo, safe='')}"

    def _get_paginated_list(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        page = 1
        per_page = 100
        output: list[dict[str, Any]] = []
        while True:
            query = dict(params or {})
            query["per_page"] = per_page
            query["page"] = page
            data = self._request("GET", path, params=query, expect_json=True)
            if not isinstance(data, list):
                break
            page_items = [item for item in data if isinstance(item, dict)]
            if not page_items:
                break
            output.extend(page_items)
            if limit is not None and len(output) >= limit:
                return output[:limit]
            if len(page_items) < per_page:
                break
            page += 1
        return output

    def _get_repository_default_branch(self, repo: str) -> str:
        cache_key = repo.strip().lower()
        cached = self._default_branch_cache.get(cache_key)
        if cached:
            return cached

        data = self._request_json("GET", f"{self._repo_prefix(repo)}")
        branch = str(data.get("default_branch") or "").strip() or "main"
        self._default_branch_cache[cache_key] = branch
        return branch

    def list_projects(self) -> list[dict[str, Any]]:
        org = self._require_github_org()
        return [
            {
                "id": org,
                "name": org,
                "state": "active",
                "url": f"https://github.com/{org}",
            }
        ]

    def list_repositories(self) -> list[dict[str, Any]]:
        org = self._require_github_org()
        repos = self._get_paginated_list(f"/orgs/{quote(org, safe='')}/repos")
        return [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "defaultBranch": item.get("default_branch"),
                "webUrl": item.get("html_url"),
            }
            for item in repos
            if isinstance(item, dict)
        ]

    def search_code(
        self,
        *,
        query: str,
        project: str | None = None,
        repos: list[str] | None = None,
        skip: int = 0,
        take: int = 20,
    ) -> dict[str, Any]:
        org = self._require_github_org()
        effective_repos = [item for item in (repos or []) if item]
        if project and not effective_repos:
            effective_repos = [project]

        search_targets: list[str | None] = effective_repos or [None]
        desired = max(1, skip + take)
        all_items: list[dict[str, Any]] = []
        total_count = 0

        for target_repo in search_targets:
            page = 1
            while len(all_items) < desired:
                remaining = max(1, desired - len(all_items))
                per_page = min(100, remaining)
                qualifiers = [query]
                if target_repo:
                    qualifiers.append(f"repo:{org}/{target_repo}")
                else:
                    qualifiers.append(f"org:{org}")
                q = " ".join(part for part in qualifiers if part.strip())
                data = self._request_json(
                    "GET",
                    "/search/code",
                    params={"q": q, "per_page": per_page, "page": page},
                )
                if page == 1:
                    total_count += int(data.get("total_count", 0))

                items = data.get("items", [])
                page_items = [entry for entry in items if isinstance(entry, dict)]
                if not page_items:
                    break
                all_items.extend(page_items)
                if len(page_items) < per_page:
                    break
                page += 1

        sliced = all_items[max(0, skip) : max(0, skip) + max(1, take)]
        results: list[str] = []
        for item in sliced:
            repository = item.get("repository") or {}
            repo_name = str(repository.get("name") or "")
            path = str(item.get("path") or "")
            results.append(f"{org}/{repo_name}:/{path}")

        if not total_count:
            total_count = len(all_items)
        return {"matchesCount": total_count, "results": results}

    def _get_repository_files(
        self,
        *,
        repo: str,
        path: str | None,
        branch: str | None,
    ) -> list[dict[str, Any]]:
        normalized_path = normalize_path(path)
        ref = normalize_branch_name(branch) or self._get_repository_default_branch(repo)
        prefix = normalized_path.strip("/")
        repo_prefix = self._repo_prefix(repo)

        def _tree_entries(treeish: str, *, recursive: bool) -> list[dict[str, Any]]:
            encoded_treeish = quote(treeish, safe="")
            params = {"recursive": "1"} if recursive else None
            data = self._request_json(
                "GET",
                f"{repo_prefix}/git/trees/{encoded_treeish}",
                params=params,
            )
            entries = data.get("tree", [])
            if not isinstance(entries, list):
                return []
            return [item for item in entries if isinstance(item, dict)]

        def _to_file_entry(entry_path: str, *, sha: str | None) -> dict[str, Any]:
            return {
                "path": f"/{entry_path.lstrip('/')}",
                "is_binary": False,
                "sha": sha,
            }

        if not prefix:
            root_tree = _tree_entries(ref, recursive=True)
            output: list[dict[str, Any]] = []
            for item in root_tree:
                if item.get("type") != "blob":
                    continue
                output.append(
                    _to_file_entry(
                        str(item.get("path") or ""),
                        sha=str(item.get("sha") or "") or None,
                    )
                )
            return output

        path_parts = [part for part in prefix.split("/") if part]
        current_entries = _tree_entries(ref, recursive=False)
        resolved_parts: list[str] = []

        for index, part in enumerate(path_parts):
            matched = next(
                (item for item in current_entries if str(item.get("path") or "") == part),
                None,
            )
            if not isinstance(matched, dict):
                return []

            node_type = str(matched.get("type") or "")
            resolved_parts.append(part)
            is_last = index == len(path_parts) - 1

            if is_last and node_type == "blob":
                return [
                    _to_file_entry(
                        "/".join(resolved_parts),
                        sha=str(matched.get("sha") or "") or None,
                    )
                ]

            if node_type != "tree":
                return []

            tree_sha = str(matched.get("sha") or "").strip()
            if not tree_sha:
                return []

            if is_last:
                subtree = _tree_entries(tree_sha, recursive=True)
                subtree_prefix = "/".join(resolved_parts)
                output = []
                for item in subtree:
                    if item.get("type") != "blob":
                        continue
                    rel_path = str(item.get("path") or "")
                    full_path = f"{subtree_prefix}/{rel_path}" if rel_path else subtree_prefix
                    output.append(
                        _to_file_entry(
                            full_path,
                            sha=str(item.get("sha") or "") or None,
                        )
                    )
                return output

            current_entries = _tree_entries(tree_sha, recursive=False)

        return []

    def _get_file_text(
        self,
        *,
        repo: str,
        file_path: str,
        branch: str | None,
        blob_sha: str | None = None,
        session: requests.Session | None = None,
    ) -> str:
        if blob_sha:
            try:
                blob_data = self._request_json(
                    "GET",
                    f"{self._repo_prefix(repo)}/git/blobs/{quote(blob_sha, safe='')}",
                    session=session,
                )
                blob_content = blob_data.get("content")
                blob_encoding = str(blob_data.get("encoding") or "")
                if isinstance(blob_content, str) and blob_encoding.lower() == "base64":
                    decoded = base64.b64decode(blob_content.encode("utf-8"))
                    return decoded.decode("utf-8", errors="replace")
                if isinstance(blob_content, str):
                    return blob_content
            except Exception:
                pass

        ref = normalize_branch_name(branch) or self._get_repository_default_branch(repo)
        encoded_path = quote(file_path.lstrip("/"), safe="/")
        data = self._request_json(
            "GET",
            f"{self._repo_prefix(repo)}/contents/{encoded_path}",
            params={"ref": ref},
            session=session,
        )
        content = data.get("content")
        encoding = str(data.get("encoding") or "")
        if isinstance(content, str) and encoding.lower() == "base64":
            decoded = base64.b64decode(content.encode("utf-8"))
            return decoded.decode("utf-8", errors="replace")
        if isinstance(content, str):
            return content
        return self._request_text(
            "GET",
            f"{self._repo_prefix(repo)}/contents/{encoded_path}",
            params={"ref": ref},
            session=session,
        )

    @staticmethod
    def _default_grep_workers(candidate_files: int) -> int:
        if candidate_files <= 1:
            return 1
        if candidate_files <= 8:
            return 4
        if candidate_files <= 32:
            return 8
        if candidate_files <= 96:
            return 16
        return 20

    def grep(
        self,
        *,
        repo: str,
        pattern: str | None = None,
        path: str | None = None,
        branch: str | None = None,
        glob: str | None = None,
        output_mode: Literal["content", "files_with_matches", "count"] = "content",
        case_insensitive: bool = True,
        context_lines: int | None = 3,
        from_line: int | None = None,
        to_line: int | None = None,
    ) -> dict[str, Any]:
        regex_pattern = pattern or ".*"
        is_match_all = match_all_pattern(regex_pattern)
        file_regex = glob_to_regex(glob) if glob else ".*"
        filename_filter = re.compile(file_regex)
        resolved_branch = normalize_branch_name(branch) or self._get_repository_default_branch(repo)
        files = self._get_repository_files(repo=repo, path=path, branch=resolved_branch)
        matching = [
            item
            for item in files
            if filename_filter.search(os.path.basename(str(item.get("path", ""))))
        ]

        if output_mode == "files_with_matches" and is_match_all:
            text = "\n".join(str(item.get("path", "")) for item in matching)
            text = truncate_output(
                text,
                self.max_output_chars,
                "Narrow results with a more specific path or glob filter.",
            )
            return {
                "text": text,
                "files_matched": len(matching),
                "warnings": [],
                "partial": False,
            }

        search_pattern, compile_error = compile_search_pattern(
            regex_pattern,
            case_insensitive=case_insensitive,
        )
        if compile_error:
            return {
                "text": compile_error,
                "files_matched": 0,
                "warnings": [],
                "partial": False,
            }

        output_lines: list[str] = []
        warnings: list[str] = []
        files_matched = 0

        grep_parallel_enabled = parse_bool_env(
            "GITHUB_GREP_ENABLE_PARALLEL",
            default=True,
        )
        grep_max_workers = parse_int_env(
            "GITHUB_GREP_MAX_WORKERS",
            default=self._default_grep_workers(len(matching)),
            min_value=1,
            max_value=32,
        )
        use_parallel = grep_parallel_enabled and grep_max_workers > 1 and len(matching) > 1

        def _process_file(
            file_path: str,
            blob_sha: str | None,
            *,
            session: requests.Session | None = None,
        ) -> tuple[list[str], int, str | None]:
            try:
                content = self._get_file_text(
                    repo=repo,
                    file_path=file_path,
                    branch=resolved_branch,
                    blob_sha=blob_sha,
                    session=session,
                )
            except Exception as exc:
                return [], 0, f"failed to read {file_path}: {exc}"

            lines = slice_lines(
                content.splitlines(),
                from_line=from_line,
                to_line=to_line,
            )

            match_line_nums = {idx for idx, line in enumerate(lines) if search_pattern.search(line)}
            if not match_line_nums:
                return [], 0, None

            if output_mode == "files_with_matches":
                return [file_path], 1, None
            if output_mode == "count":
                return [f"{file_path}:{len(match_line_nums)}"], 1, None
            return (
                format_grep_matches(
                    file_path,
                    lines,
                    match_line_nums,
                    context_lines or 0,
                    include_line_numbers=True,
                ),
                1,
                None,
            )

        def _process_file_in_worker(
            file_path: str,
            blob_sha: str | None,
        ) -> tuple[list[str], int, str | None]:
            return _process_file(
                file_path,
                blob_sha,
                session=self._get_http_session(),
            )

        file_entries = [
            (
                str(item.get("path", "")),
                str(item.get("sha") or "") or None,
            )
            for item in matching
        ]
        if use_parallel:
            max_workers = min(grep_max_workers, len(file_entries))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [
                    executor.submit(_process_file_in_worker, file_path, blob_sha)
                    for file_path, blob_sha in file_entries
                ]
                for (file_path, _blob_sha), future in zip(file_entries, futures):
                    try:
                        lines_out, matched_count, warning = future.result()
                    except Exception as exc:
                        warnings.append(f"failed to read {file_path}: {exc}")
                        continue
                    if warning:
                        warnings.append(warning)
                        continue
                    files_matched += matched_count
                    output_lines.extend(lines_out)
        else:
            for file_path, blob_sha in file_entries:
                lines_out, matched_count, warning = _process_file(file_path, blob_sha)
                if warning:
                    warnings.append(warning)
                    continue
                files_matched += matched_count
                output_lines.extend(lines_out)

        text = "\n".join(output_lines)
        text = truncate_output(
            text,
            self.max_output_chars,
            "Use from_line/to_line to read specific ranges, or narrow with path/glob/pattern.",
        )
        return {
            "text": text,
            "files_matched": files_matched,
            "warnings": warnings,
            "partial": bool(warnings),
        }

    @staticmethod
    def _pr_status(pr: dict[str, Any]) -> str:
        state = str(pr.get("state") or "").lower()
        if state == "open":
            return "active"
        if pr.get("merged_at"):
            return "completed"
        return "abandoned"

    def list_pull_requests(
        self,
        *,
        repos: list[str] | None = None,
        statuses: list[str] | None = None,
        creators: list[str] | None = None,
        date_from: str | datetime | None = None,
        date_to: str | datetime | None = None,
        skip: int = 0,
        take: int = 100,
        exclude_drafts: bool = False,
        include_labels: bool = False,
    ) -> dict[str, Any]:
        allowed_status = {"active", "completed", "abandoned"}
        effective_status = statuses or ["active", "completed", "abandoned"]
        normalized_status: list[str] = []
        for status in effective_status:
            lowered = status.strip().lower()
            if lowered not in allowed_status:
                raise ValueError("status must be one of: active, completed, abandoned")
            if lowered not in normalized_status:
                normalized_status.append(lowered)

        repo_names = [item for item in (repos or []) if item]
        if not repo_names:
            repo_names = [entry["name"] for entry in self.list_repositories() if entry.get("name")]

        creator_filter = [item.lower() for item in creators or []]
        from_dt = parse_iso_datetime(date_from)
        to_dt = parse_iso_datetime(date_to)
        desired_count = max(1, max(0, skip) + max(1, take))
        single_repo_mode = len(repo_names) == 1

        states_to_fetch: list[str] = []
        if "active" in normalized_status:
            states_to_fetch.append("open")
        if any(item in normalized_status for item in {"completed", "abandoned"}):
            states_to_fetch.append("closed")
        if not states_to_fetch:
            states_to_fetch = ["all"]

        output: list[dict[str, Any]] = []
        for repo_name in repo_names:
            for github_state in states_to_fetch:
                page = 1
                per_page = 100
                while True:
                    pulls_data = self._request(
                        "GET",
                        f"{self._repo_prefix(repo_name)}/pulls",
                        params={"state": github_state, "per_page": per_page, "page": page},
                        expect_json=True,
                    )
                    if not isinstance(pulls_data, list):
                        break
                    pulls = [item for item in pulls_data if isinstance(item, dict)]
                    if not pulls:
                        break

                    for item in pulls:
                        status = self._pr_status(item)
                        if status not in normalized_status:
                            continue
                        if exclude_drafts and bool(item.get("draft")):
                            continue

                        user = item.get("user") or {}
                        creator = str(user.get("login") or "")
                        if creator_filter and not any(token in creator.lower() for token in creator_filter):
                            continue

                        created_dt = parse_iso_datetime(item.get("created_at"))
                        closed_dt = parse_iso_datetime(item.get("closed_at"))
                        reference_dt = closed_dt if status in {"completed", "abandoned"} else created_dt
                        if from_dt and reference_dt and reference_dt < from_dt:
                            continue
                        if to_dt and reference_dt and reference_dt > to_dt:
                            continue

                        labels: list[str] = []
                        if include_labels:
                            for label in item.get("labels") or []:
                                if isinstance(label, dict):
                                    name = str(label.get("name") or "").strip()
                                    if name:
                                        labels.append(name)

                        output.append(
                            {
                                "pr_id": item.get("number"),
                                "title": item.get("title") or "",
                                "created_by": creator,
                                "status": status,
                                "creation_date": item.get("created_at"),
                                "project_name": self._require_github_org(),
                                "repository_name": repo_name,
                                "repository_id": item.get("id"),
                                "closed_date": (
                                    closed_dt.astimezone(UTC).strftime("%Y-%m-%d")
                                    if closed_dt
                                    else None
                                ),
                                "source_branch": item.get("head", {}).get("ref"),
                                "target_branch": item.get("base", {}).get("ref"),
                                "target_ref": item.get("base", {}).get("ref"),
                                "labels": labels,
                            }
                        )

                    if single_repo_mode and len(output) >= desired_count:
                        break
                    if len(pulls) < per_page:
                        break
                    page += 1
                if single_repo_mode and len(output) >= desired_count:
                    break

        output.sort(key=lambda row: str(row.get("creation_date") or ""), reverse=True)
        paged = output[max(0, skip) : max(0, skip) + max(1, take)]
        has_more = len(output) > max(0, skip) + len(paged)
        return {
            "returned_count": len(paged),
            "has_more": has_more,
            "results": paged,
        }

    def get_pull_request(self, *, repo: str, pull_request_id: int) -> dict[str, Any]:
        pr = self._request_json("GET", f"{self._repo_prefix(repo)}/pulls/{pull_request_id}")
        files = self._get_paginated_list(
            f"{self._repo_prefix(repo)}/pulls/{pull_request_id}/files",
            limit=2000,
        )
        changed_files = [str(item.get("filename")) for item in files if item.get("filename")]
        threads_data = self.get_pull_request_threads(repo=repo, pull_request_id=pull_request_id)
        mapped_pr = {
            "pullRequestId": pr.get("number"),
            "title": pr.get("title"),
            "status": self._pr_status(pr),
            "createdBy": {"displayName": (pr.get("user") or {}).get("login", "")},
            "sourceRefName": f"refs/heads/{(pr.get('head') or {}).get('ref', '')}",
            "targetRefName": f"refs/heads/{(pr.get('base') or {}).get('ref', '')}",
        }
        return {
            "pull_request": mapped_pr,
            "threads": threads_data.get("threads", []),
            "changed_files": changed_files,
        }

    def get_pull_request_threads(
        self,
        *,
        repo: str,
        pull_request_id: int,
        include_deleted: bool = False,
    ) -> dict[str, Any]:
        issue_comments = self._get_paginated_list(
            f"{self._repo_prefix(repo)}/issues/{pull_request_id}/comments"
        )
        review_comments = self._get_paginated_list(
            f"{self._repo_prefix(repo)}/pulls/{pull_request_id}/comments"
        )

        threads: list[dict[str, Any]] = []
        total_comments = 0

        for comment in issue_comments:
            if not isinstance(comment, dict):
                continue
            deleted = bool(comment.get("isDeleted", False))
            if deleted and not include_deleted:
                continue
            author = (comment.get("user") or {}).get("login", "")
            comment_payload = {
                "id": comment.get("id"),
                "author": author,
                "content": str(comment.get("body") or ""),
                "comment_type": "text",
                "is_deleted": deleted,
                "published_date": comment.get("created_at"),
                "last_updated_date": comment.get("updated_at"),
            }
            threads.append(
                {
                    "id": f"issue-{comment.get('id')}",
                    "status": None,
                    "is_deleted": deleted,
                    "file_path": None,
                    "line_start": None,
                    "line_end": None,
                    "comments": [comment_payload],
                    "comment_count": 1,
                    "published_date": comment.get("created_at"),
                    "last_updated_date": comment.get("updated_at"),
                }
            )
            total_comments += 1

        for comment in review_comments:
            if not isinstance(comment, dict):
                continue
            deleted = bool(comment.get("isDeleted", False))
            if deleted and not include_deleted:
                continue
            author = (comment.get("user") or {}).get("login", "")
            comment_payload = {
                "id": comment.get("id"),
                "author": author,
                "content": str(comment.get("body") or ""),
                "comment_type": "text",
                "is_deleted": deleted,
                "published_date": comment.get("created_at"),
                "last_updated_date": comment.get("updated_at"),
            }
            line = comment.get("line") or comment.get("original_line")
            threads.append(
                {
                    "id": f"review-{comment.get('id')}",
                    "status": None,
                    "is_deleted": deleted,
                    "file_path": f"/{str(comment.get('path') or '').lstrip('/')}" if comment.get("path") else None,
                    "line_start": line,
                    "line_end": line,
                    "comments": [comment_payload],
                    "comment_count": 1,
                    "published_date": comment.get("created_at"),
                    "last_updated_date": comment.get("updated_at"),
                }
            )
            total_comments += 1

        return {
            "pull_request_id": pull_request_id,
            "project_name": self._require_github_org(),
            "repository_name": repo,
            "returned_count": len(threads),
            "total_comments": total_comments,
            "threads": threads,
        }

    def get_build_log(self, *, repo: str, build_id: int) -> dict[str, Any]:
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
        self,
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
        self,
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
        regex_pattern = pattern or ".*"
        is_match_all = match_all_pattern(regex_pattern)
        if log_id is None and is_match_all:
            return {
                "text": (
                    "Error: Specify a log_id to read full content, or provide a pattern to search across all logs."
                ),
                "logs_matched": 0,
                "warnings": [],
                "partial": False,
            }

        search_pattern, compile_error = compile_search_pattern(
            regex_pattern,
            case_insensitive=case_insensitive,
        )
        if compile_error:
            return {
                "text": compile_error,
                "logs_matched": 0,
                "warnings": [],
                "partial": False,
            }

        if log_id is not None and is_match_all:
            content = self.get_build_log_content(repo=repo, log_id=log_id)
            lines = slice_lines(
                content.splitlines(),
                from_line=from_line,
                to_line=to_line,
            )

            if output_mode == "logs_with_matches":
                return {"text": f"Log {log_id}", "logs_matched": 1, "warnings": [], "partial": False}
            if output_mode == "count":
                return {
                    "text": f"Log {log_id}:{len(lines)}",
                    "logs_matched": 1,
                    "warnings": [],
                    "partial": False,
                }
            start = from_line or 1
            text = "\n".join(f"{start + idx}:{line}" for idx, line in enumerate(lines))
            text = truncate_output(
                text,
                self.max_output_chars,
                "Use from_line/to_line to read specific ranges.",
            )
            return {"text": text, "logs_matched": 1, "warnings": [], "partial": False}

        if log_id is not None:
            log_ids = [log_id]
        else:
            build_logs = self.get_build_log(repo=repo, build_id=build_id)
            log_ids = [
                int(item["id"])
                for item in build_logs.get("logs", [])
                if isinstance(item, dict) and item.get("id") is not None
            ]

        output_lines: list[str] = []
        warnings: list[str] = []
        logs_matched = 0

        for current_log_id in log_ids:
            try:
                content = self.get_build_log_content(repo=repo, log_id=current_log_id)
            except Exception as exc:
                warnings.append(f"failed to read Log {current_log_id}: {exc}")
                continue

            lines = slice_lines(
                content.splitlines(),
                from_line=from_line,
                to_line=to_line,
            )

            matches = {idx for idx, line in enumerate(lines) if search_pattern.search(line)}
            if not matches:
                continue
            logs_matched += 1

            if output_mode == "logs_with_matches":
                output_lines.append(f"Log {current_log_id}")
                continue
            if output_mode == "count":
                output_lines.append(f"Log {current_log_id}:{len(matches)}")
                continue

            output_lines.extend(
                format_grep_matches(
                    f"Log {current_log_id}",
                    lines,
                    matches,
                    context_lines or 0,
                    include_line_numbers=True,
                )
            )

        text = "\n".join(output_lines)
        text = truncate_output(
            text,
            self.max_output_chars,
            "Use from_line/to_line to read specific ranges, or narrow with pattern/log-id.",
        )
        return {
            "text": text,
            "logs_matched": logs_matched,
            "warnings": warnings,
            "partial": bool(warnings),
        }

    def _issue_to_work_item(self, issue: dict[str, Any], repo: str) -> dict[str, Any]:
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

    def get_ticket_by_id(self, *, repo: str, work_item_id: int) -> dict[str, Any]:
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
        self,
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
        self,
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
