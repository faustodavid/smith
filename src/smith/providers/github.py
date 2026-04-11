from __future__ import annotations

import math
import os
import subprocess
import threading
import time
from typing import Any
from urllib.parse import quote

import requests

from smith.config import RuntimeConfig
from smith.errors import SmithApiError, SmithAuthError
from smith.http import parse_rate_limit_reset_seconds, parse_retry_after_seconds
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
    def __init__(
        self,
        *,
        config: RuntimeConfig,
        session: requests.Session,
        github_org: str | None = None,
        github_api_url: str | None = None,
        github_api_version: str | None = None,
        token_env: str | None = None,
    ) -> None:
        super().__init__(config=config, session=session, token_env=token_env)
        self.github_org = github_org or config.github_org
        self.github_api_url = github_api_url or config.github_api_url
        self.github_api_version = github_api_version or config.github_api_version
        self.max_output_chars = config.max_output_chars
        self._github_token: str | None = None
        self._default_branch_cache: dict[str, str] = {}
        self._repository_list_cache: list[dict[str, Any]] | None = None
        self._github_request_semaphore = threading.BoundedSemaphore(config.github_max_concurrent_requests)
        self._github_cooldown_lock = threading.Lock()
        self._github_cooldown_until_monotonic = 0.0

    def _get_token(self, *, force_refresh: bool = False) -> str:
        if self._github_token and not force_refresh:
            return self._github_token

        token_env_var = self._token_env or "GITHUB_TOKEN"
        env_token = os.getenv(token_env_var, "").strip()
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

    @staticmethod
    def _response_text(response: Any) -> str:
        return str(getattr(response, "text", "") or "")

    def _response_message_text(self, response: Any) -> str:
        text = self._response_text(response).strip()
        if text:
            lowered = text.lower()
        else:
            lowered = ""
        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            message = str(payload.get("message") or "").strip().lower()
            if message and message not in lowered:
                return f"{lowered}\n{message}" if lowered else message
        return lowered

    def _is_github_rate_limited_response(self, response: Any) -> bool:
        status_code = int(getattr(response, "status_code", 0))
        if status_code == 429:
            return True
        if status_code != 403:
            return False
        headers = getattr(response, "headers", {}) or {}
        if str(headers.get("Retry-After") or "").strip():
            return True
        if str(headers.get("X-RateLimit-Remaining") or "").strip() == "0":
            return True
        message = self._response_message_text(response)
        return "secondary rate limit" in message or "api rate limit exceeded" in message

    def _format_rate_limit_hint(self, response: Any) -> str:
        retry_after = parse_retry_after_seconds(response)
        if retry_after is not None:
            return f" Retry after about {int(math.ceil(retry_after))}s."
        reset_after = parse_rate_limit_reset_seconds(response)
        if reset_after is not None:
            return f" Retry after about {int(math.ceil(reset_after))}s."
        return ""

    def _github_rate_limit_delay_seconds(self, response: Any, retry_index: int) -> float:
        retry_after = parse_retry_after_seconds(response)
        if retry_after is not None:
            if retry_after > self._config.github_rate_limit_max_sleep_seconds:
                raise SmithApiError(
                    "GitHub API rate limited. "
                    f"Retry-After requested {int(math.ceil(retry_after))}s which exceeds "
                    f"GITHUB_RATE_LIMIT_MAX_SLEEP_SECONDS={self._config.github_rate_limit_max_sleep_seconds}.",
                    status_code=int(getattr(response, "status_code", 0)) or None,
                )
            return retry_after

        reset_after = parse_rate_limit_reset_seconds(response)
        if reset_after is not None:
            if reset_after > self._config.github_rate_limit_max_sleep_seconds:
                raise SmithApiError(
                    "GitHub API rate limited. "
                    f"X-RateLimit-Reset requested {int(math.ceil(reset_after))}s which exceeds "
                    f"GITHUB_RATE_LIMIT_MAX_SLEEP_SECONDS={self._config.github_rate_limit_max_sleep_seconds}.",
                    status_code=int(getattr(response, "status_code", 0)) or None,
                )
            return reset_after

        return super()._retry_sleep_seconds(response=response, retry_index=retry_index)

    def _github_cooldown_sleep_seconds(self) -> float:
        with self._github_cooldown_lock:
            return max(0.0, self._github_cooldown_until_monotonic - time.monotonic())

    def _perform_http_request(
        self,
        http_session: Any,
        *,
        method: str,
        resolved_url: str,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        headers: dict[str, str],
        timeout: int,
    ) -> Any:
        while True:
            cooldown_sleep = self._github_cooldown_sleep_seconds()
            if cooldown_sleep > 0:
                time.sleep(cooldown_sleep)
                continue

            self._github_request_semaphore.acquire()
            try:
                cooldown_sleep = self._github_cooldown_sleep_seconds()
                if cooldown_sleep > 0:
                    continue
                return http_session.request(
                    method,
                    resolved_url,
                    params=params,
                    json=json_body,
                    headers=headers,
                    timeout=timeout,
                )
            finally:
                self._github_request_semaphore.release()

    def _should_refresh_auth_response(self, response: Any) -> bool:
        status_code = int(getattr(response, "status_code", 0))
        if status_code == 401:
            return True
        return status_code == 403 and not self._is_github_rate_limited_response(response)

    def _is_retryable_response(self, response: Any) -> bool:
        return self._is_github_rate_limited_response(response) or super()._is_retryable_response(response)

    def _is_auth_failure_response(self, response: Any) -> bool:
        status_code = int(getattr(response, "status_code", 0))
        if status_code == 401:
            return True
        return status_code == 403 and not self._is_github_rate_limited_response(response)

    def _record_retry_cooldown(self, response: Any, retry_index: int, sleep_seconds: float) -> None:
        if not self._is_github_rate_limited_response(response):
            return
        deadline = time.monotonic() + max(0.0, sleep_seconds)
        with self._github_cooldown_lock:
            self._github_cooldown_until_monotonic = max(self._github_cooldown_until_monotonic, deadline)

    def _retry_sleep_seconds(self, *, response: Any, retry_index: int) -> float:
        if response is not None and self._is_github_rate_limited_response(response):
            return self._github_rate_limit_delay_seconds(response, retry_index)
        return super()._retry_sleep_seconds(response=response, retry_index=retry_index)

    def _handle_response_status(self, response: Any, resolved_url: str) -> None:
        if self._is_github_rate_limited_response(response):
            hint = self._format_rate_limit_hint(response)
            raise SmithApiError(
                f"GitHub API rate limited (HTTP {response.status_code}). Narrow scope and retry.{hint}".strip(),
                status_code=response.status_code,
            )

    def _require_github_org(self) -> str:
        org = (self.github_org or "").strip()
        if not org:
            raise ValueError("GitHub remote is missing an org in the Smith config.")
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
