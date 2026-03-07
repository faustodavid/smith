from __future__ import annotations

import os
import subprocess
from typing import Any
from urllib.parse import quote

import requests

from smith.config import RuntimeConfig
from smith.errors import SmithApiError, SmithAuthError
from smith.providers.base import BaseProvider
from smith.providers.github_builds import GitHubBuildMixin
from smith.providers.github_code import GitHubCodeMixin
from smith.providers.github_issues import GitHubIssueMixin
from smith.providers.github_pull_requests import GitHubPullRequestMixin

GITHUB_DEFAULT_API_URL = "https://api.github.com"
GITHUB_DEFAULT_API_VERSION = "2022-11-28"


class GitHubProvider(
    GitHubCodeMixin,
    GitHubPullRequestMixin,
    GitHubBuildMixin,
    GitHubIssueMixin,
    BaseProvider,
):
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
