from __future__ import annotations

import os
import subprocess
from typing import Any
from urllib.parse import quote, urlparse

import requests

from smith.config import RuntimeConfig
from smith.errors import SmithAuthError
from smith.providers.base import BaseProvider
from smith.providers.gitlab_builds import GitLabBuildMixin
from smith.providers.gitlab_code import GitLabCodeMixin
from smith.providers.gitlab_issues import GitLabIssueMixin
from smith.providers.gitlab_pull_requests import GitLabPullRequestMixin

GITLAB_DEFAULT_API_URL = "https://gitlab.com/api/v4"


class GitLabProvider(
    GitLabCodeMixin,
    GitLabPullRequestMixin,
    GitLabBuildMixin,
    GitLabIssueMixin,
    BaseProvider,
):
    def __init__(
        self,
        *,
        config: RuntimeConfig,
        session: requests.Session,
        gitlab_group: str | None = None,
        gitlab_api_url: str | None = None,
        token_env: str | None = None,
    ) -> None:
        super().__init__(config=config, session=session, token_env=token_env)
        self.gitlab_group = gitlab_group or config.gitlab_group
        self.gitlab_api_url = gitlab_api_url or config.gitlab_api_url
        self.max_output_chars = config.max_output_chars
        self._gitlab_token: str | None = None
        self._default_branch_cache: dict[str, str] = {}
        self._repository_list_cache: list[dict[str, Any]] | None = None
        self._project_id_to_path_cache: dict[str, str] = {}
        self._project_key_to_path_cache: dict[str, str] = {}

    def _gitlab_host(self) -> str:
        parsed = urlparse(self.gitlab_api_url if "://" in self.gitlab_api_url else f"https://{self.gitlab_api_url}")
        return (parsed.netloc or parsed.path or "").strip().strip("/")

    @staticmethod
    def _is_likely_token(token: str) -> bool:
        return bool(token) and all(not char.isspace() for char in token)

    def _get_token(self, *, force_refresh: bool = False) -> str:
        if self._gitlab_token and not force_refresh:
            return self._gitlab_token

        token_env_var = self._token_env or "GITLAB_TOKEN"
        env_token = os.getenv(token_env_var, "").strip()
        if env_token:
            self._gitlab_token = env_token
            return self._gitlab_token

        host = self._gitlab_host()
        token_commands = (
            [["glab", "config", "get", "token", "--host", host]]
            if host
            else [["glab", "config", "get", "token"]]
        )
        last_error: Exception | None = None
        saw_command_success = False
        token = ""
        for command in token_commands:
            try:
                result = subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except Exception as exc:
                last_error = exc
                continue
            saw_command_success = True
            token = result.stdout.strip()
            if self._is_likely_token(token):
                break
            token = ""

        if not token:
            message = "Failed to acquire GitLab token. Set GITLAB_TOKEN or run `glab auth login`."
            if host:
                message = (
                    f"Failed to acquire GitLab token for {host}. "
                    f"Set GITLAB_TOKEN or run `glab auth login --hostname {host}`."
                )
            if saw_command_success:
                message = "GitLab token is empty. Set GITLAB_TOKEN or run `glab auth login`."
                if host:
                    message = (
                        f"GitLab token is empty for {host}. "
                        f"Set GITLAB_TOKEN or run `glab auth login --hostname {host}`."
                    )
            raise SmithAuthError(message) from last_error

        self._gitlab_token = token
        return self._gitlab_token

    def _auth_error_message(self) -> str:
        return (
            "GitLab authentication rejected with HTTP 401/403. "
            "Set GITLAB_TOKEN or run `glab auth login` and retry."
        )

    def _timeout(self) -> int:
        return self._config.gitlab_timeout_seconds

    def _build_url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.gitlab_api_url}{path}"

    def _require_gitlab_group(self) -> str:
        group = (self.gitlab_group or "").strip().strip("/")
        if not group:
            raise ValueError("Missing GITLAB_GROUP. Example: export GITLAB_GROUP=<group>")
        return group

    def _gitlab_web_url(self) -> str:
        if self.gitlab_api_url.endswith("/api/v4"):
            return self.gitlab_api_url[: -len("/api/v4")]
        if "/api/" in self.gitlab_api_url:
            return self.gitlab_api_url.split("/api/", 1)[0]
        return self.gitlab_api_url

    def _cache_project(
        self,
        *,
        project_id: str | None,
        full_path: str,
        relative_path: str | None = None,
        default_branch: str | None = None,
    ) -> None:
        normalized_full_path = full_path.strip().strip("/")
        if not normalized_full_path:
            return

        relative = (relative_path or self._relative_repo_path(normalized_full_path)).strip().strip("/")
        if project_id:
            self._project_id_to_path_cache[str(project_id)] = normalized_full_path

        self._project_key_to_path_cache[normalized_full_path.lower()] = normalized_full_path
        if relative:
            self._project_key_to_path_cache[relative.lower()] = normalized_full_path
        if default_branch:
            self._default_branch_cache[normalized_full_path.lower()] = default_branch
            if relative:
                self._default_branch_cache[relative.lower()] = default_branch

    def _relative_repo_path(self, full_path: str) -> str:
        normalized_full_path = full_path.strip().strip("/")
        group = self._require_gitlab_group()
        prefix = f"{group}/"
        if normalized_full_path.startswith(prefix):
            relative = normalized_full_path[len(prefix) :]
            if relative:
                return relative
        return normalized_full_path

    def _full_project_path(self, repo: str) -> str:
        normalized_repo = repo.strip().strip("/")
        if not normalized_repo:
            raise ValueError("Repository is required.")

        cached = self._project_key_to_path_cache.get(normalized_repo.lower())
        if cached:
            return cached

        group = self._require_gitlab_group()
        if normalized_repo.startswith(f"{group}/"):
            return normalized_repo
        return f"{group}/{normalized_repo}"

    def _project_id(self, repo: str) -> str:
        return quote(self._full_project_path(repo), safe="")

    def _project_path_from_id(self, project_id: str | int | None) -> str | None:
        project_id_text = str(project_id or "").strip()
        if not project_id_text:
            return None

        cached = self._project_id_to_path_cache.get(project_id_text)
        if cached:
            return cached

        data = self._request_json("GET", f"/projects/{quote(project_id_text, safe='')}")
        full_path = str(data.get("path_with_namespace") or "").strip().strip("/")
        if not full_path:
            return None

        self._cache_project(
            project_id=project_id_text,
            full_path=full_path,
            default_branch=str(data.get("default_branch") or "") or None,
        )
        return full_path

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
            query["per_page"] = min(per_page, max(1, limit - len(output))) if limit is not None else per_page
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
            if len(page_items) < query["per_page"]:
                break
            page += 1
        return output

    def _get_project_default_branch(self, repo: str) -> str:
        cache_key = repo.strip().strip("/").lower()
        cached = self._default_branch_cache.get(cache_key)
        if cached:
            return cached

        data = self._request_json("GET", f"/projects/{self._project_id(repo)}")
        branch = str(data.get("default_branch") or "").strip() or "main"
        full_path = str(data.get("path_with_namespace") or "").strip().strip("/")
        self._cache_project(
            project_id=str(data.get("id") or "") or None,
            full_path=full_path or self._full_project_path(repo),
            default_branch=branch,
        )
        return branch
