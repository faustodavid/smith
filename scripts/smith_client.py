#!/usr/bin/env python3
from __future__ import annotations

import base64
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Literal
from urllib.parse import quote, urlparse

import requests
from requests.adapters import HTTPAdapter
from azure.identity import DefaultAzureCredential

from smith_format import (
    format_grep_matches,
    glob_to_regex,
    normalize_branch_name,
    truncate_output,
)

ADO_SCOPE = "499b84ac-1321-427f-aa17-267ca6975798/.default"
GITHUB_DEFAULT_API_URL = "https://api.github.com"
GITHUB_DEFAULT_API_VERSION = "2022-11-28"


class ThanosLocalError(Exception):
    """Base error for smith."""


class ThanosLocalAuthError(ThanosLocalError):
    """Auth-specific failure."""


class ThanosLocalApiError(ThanosLocalError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ThanosLocalClient:
    def __init__(
        self,
        *,
        org_url: str | None = None,
        api_version: str | None = None,
        timeout_seconds: int | None = None,
        max_output_chars: int | None = None,
        credential: Any | None = None,
        session: requests.Session | None = None,
    ) -> None:
        env_org_url = os.getenv("AZURE_DEVOPS_ORG_URL")
        if org_url is None:
            org_url = env_org_url
        if not org_url:
            raise ValueError(
                "Missing AZURE_DEVOPS_ORG_URL. Example: export AZURE_DEVOPS_ORG_URL=https://dev.azure.com/<org>"
            )

        self.org_url = org_url.rstrip("/")
        self.api_version = api_version or os.getenv("AZURE_DEVOPS_API_VERSION", "7.1")
        self.timeout_seconds = int(os.getenv("AZURE_DEVOPS_TIMEOUT_SECONDS", timeout_seconds or 30))
        self.max_output_chars = int(
            os.getenv("THANOS_LOCAL_MAX_OUTPUT_CHARS", max_output_chars or 10240)
        )
        self.github_org = os.getenv("GITHUB_ORG", "").strip()
        self.github_api_url = os.getenv("GITHUB_API_URL", GITHUB_DEFAULT_API_URL).rstrip("/")
        self.github_api_version = os.getenv("GITHUB_API_VERSION", GITHUB_DEFAULT_API_VERSION)
        self.github_timeout_seconds = int(
            os.getenv("GITHUB_TIMEOUT_SECONDS", timeout_seconds or self.timeout_seconds)
        )
        self._http_pool_maxsize = self._parse_int_env(
            "SMITH_HTTP_POOL_MAXSIZE",
            default=32,
            min_value=1,
            max_value=256,
        )
        self._http_pool_connections = self._parse_int_env(
            "SMITH_HTTP_POOL_CONNECTIONS",
            default=16,
            min_value=1,
            max_value=256,
        )
        self._http_retry_max_attempts = self._parse_int_env(
            "SMITH_HTTP_RETRY_MAX_ATTEMPTS",
            default=2,
            min_value=1,
            max_value=6,
        )
        retry_backoff_env = os.getenv("SMITH_HTTP_RETRY_BACKOFF_SECONDS")
        try:
            parsed_backoff = float((retry_backoff_env or "").strip() or "0.4")
        except ValueError:
            parsed_backoff = 0.4
        self._http_retry_backoff_seconds = max(0.0, min(10.0, parsed_backoff))

        self._credential = credential or DefaultAzureCredential(
            exclude_interactive_browser_credential=True
        )
        self._session = session or requests.Session()
        self._access_token: str | None = None
        self._github_token: str | None = None
        self._github_default_branch_cache: dict[str, str] = {}
        self._http_thread_local = threading.local()
        self._configure_http_session(self._session)

        self.org_name = self._extract_org_name(self.org_url)

    @staticmethod
    def _extract_org_name(org_url: str) -> str:
        parsed = urlparse(org_url)
        path_bits = [bit for bit in parsed.path.split("/") if bit]
        if parsed.netloc == "dev.azure.com" and path_bits:
            return path_bits[0]

        host_bits = parsed.netloc.split(".")
        if host_bits and host_bits[0]:
            return host_bits[0]

        raise ValueError(f"Unable to infer organization name from URL: {org_url}")

    def _get_token(self, *, force_refresh: bool = False) -> str:
        if self._access_token and not force_refresh:
            return self._access_token

        try:
            token = self._credential.get_token(ADO_SCOPE)
        except Exception as exc:  # pragma: no cover - exact SDK exceptions vary by env
            raise ThanosLocalAuthError(
                "Failed to acquire Azure DevOps token using DefaultAzureCredential. "
                "Run `az login` and retry."
            ) from exc

        self._access_token = token.token
        return self._access_token

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expect_json: bool = True,
    ) -> Any:
        method_upper = method.upper()
        is_retryable_get = method_upper == "GET" and self._http_retry_max_attempts > 1
        http_session = self._get_http_session()
        request_headers = dict(headers or {})
        request_headers.setdefault("Accept", "application/json")
        response: Any = None
        for retry_index in range(self._http_retry_max_attempts):
            first_attempt_headers = dict(request_headers)
            first_attempt_headers["Authorization"] = f"Bearer {self._get_token()}"
            try:
                response = http_session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=first_attempt_headers,
                    timeout=self.timeout_seconds,
                )
            except requests.RequestException as exc:
                if is_retryable_get and retry_index < self._http_retry_max_attempts - 1:
                    time.sleep(self._retry_sleep_seconds(response=None, retry_index=retry_index))
                    continue
                raise ThanosLocalApiError(f"Request error for {url}: {exc}") from exc

            if response.status_code in (401, 403):
                retry_headers = dict(request_headers)
                retry_headers["Authorization"] = f"Bearer {self._get_token(force_refresh=True)}"
                response = http_session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=retry_headers,
                    timeout=self.timeout_seconds,
                )

            if (
                is_retryable_get
                and self._is_retryable_get_status(int(response.status_code))
                and retry_index < self._http_retry_max_attempts - 1
            ):
                time.sleep(self._retry_sleep_seconds(response=response, retry_index=retry_index))
                continue
            break

        if response is None:
            raise ThanosLocalApiError(f"No response received for {url}")

        if response.status_code in (401, 403):
            raise ThanosLocalAuthError(
                f"Authentication rejected with HTTP {response.status_code}. Run `az login` and retry."
            )

        if not 200 <= response.status_code < 300:
            text = (response.text or "").strip()
            if len(text) > 500:
                text = text[:500] + "..."
            raise ThanosLocalApiError(
                f"HTTP {response.status_code} for {url}: {text}",
                status_code=response.status_code,
            )

        if not expect_json:
            return response.text

        if response.status_code == 204:
            return {}

        body = response.text or ""
        if not body.strip():
            return {}

        try:
            return response.json()
        except ValueError as exc:
            raise ThanosLocalApiError(
                f"Expected JSON response from {url} but received invalid JSON"
            ) from exc

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        response = self._request(
            method,
            url,
            params=params,
            json_body=json_body,
            headers=headers,
            expect_json=True,
        )
        if isinstance(response, dict):
            return response
        raise ThanosLocalApiError(f"Expected dictionary response from {url}")

    def _request_text(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        response = self._request(
            method,
            url,
            params=params,
            headers=headers,
            expect_json=False,
        )
        return str(response)

    @staticmethod
    def _normalize_provider(provider: str | None) -> str:
        normalized = (provider or "azdo").strip().lower()
        if normalized not in {"azdo", "github", "all"}:
            raise ValueError("provider must be one of: azdo, github, all")
        return normalized

    @classmethod
    def _resolve_providers(cls, provider: str | None) -> list[str]:
        normalized = cls._normalize_provider(provider)
        if normalized == "all":
            return ["github", "azdo"]
        return [normalized]

    @classmethod
    def _normalize_single_provider(cls, provider: str | None, *, command: str) -> str:
        normalized = cls._normalize_provider(provider)
        if normalized == "all":
            raise ValueError(f"{command} does not support provider 'all'. Use azdo or github.")
        return normalized

    def _provider_warnings_and_partial(self, payload: Any) -> tuple[list[str], bool]:
        if not isinstance(payload, dict):
            return [], False
        warnings = payload.get("warnings")
        if isinstance(warnings, list):
            warning_list = [str(item) for item in warnings if str(item).strip()]
        else:
            warning_list = []
        partial = bool(payload.get("partial", False))
        return warning_list, partial

    def _provider_entry_success(self, payload: Any) -> dict[str, Any]:
        warnings, partial = self._provider_warnings_and_partial(payload)
        return {
            "ok": True,
            "data": payload,
            "warnings": warnings,
            "partial": partial,
            "error": None,
        }

    @staticmethod
    def _provider_entry_error(code: str, message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "data": None,
            "warnings": [],
            "partial": False,
            "error": {"code": code, "message": message},
        }

    def _fanout(
        self,
        *,
        provider: str,
        operations: dict[str, Callable[[], Any]],
    ) -> dict[str, Any]:
        requested_provider = self._normalize_provider(provider)
        providers = self._resolve_providers(requested_provider)

        provider_results: dict[str, dict[str, Any]] = {}
        succeeded: list[str] = []
        failed: list[str] = []

        def _run_provider_operation(provider_name: str) -> tuple[dict[str, Any], bool]:
            operation = operations.get(provider_name)
            if operation is None:
                return (
                    self._provider_entry_error(
                        "unsupported_provider",
                        f"Provider '{provider_name}' is not supported for this command.",
                    ),
                    False,
                )

            try:
                payload = operation()
                return self._provider_entry_success(payload), True
            except ValueError as exc:
                return self._provider_entry_error(
                    "invalid_args",
                    str(exc),
                ), False
            except ThanosLocalAuthError as exc:
                return self._provider_entry_error(
                    "auth_failure",
                    str(exc),
                ), False
            except ThanosLocalApiError as exc:
                return self._provider_entry_error(
                    "api_error",
                    str(exc),
                ), False
            except Exception as exc:  # pragma: no cover - defensive conversion
                return self._provider_entry_error(
                    "api_error",
                    f"Unexpected provider error: {exc}",
                ), False

        if len(providers) > 1:
            with ThreadPoolExecutor(max_workers=len(providers)) as executor:
                futures = {
                    provider_name: executor.submit(_run_provider_operation, provider_name)
                    for provider_name in providers
                }
                for provider_name in providers:
                    provider_entry, ok = futures[provider_name].result()
                    provider_results[provider_name] = provider_entry
                    if ok:
                        succeeded.append(provider_name)
                    else:
                        failed.append(provider_name)
        else:
            for provider_name in providers:
                provider_entry, ok = _run_provider_operation(provider_name)
                provider_results[provider_name] = provider_entry
                if ok:
                    succeeded.append(provider_name)
                else:
                    failed.append(provider_name)

        if not succeeded:
            error_codes = [
                provider_results[name]["error"]["code"]
                for name in failed
                if provider_results.get(name, {}).get("error")
            ]
            messages = [
                f"{name}: {provider_results[name]['error']['message']}"
                for name in failed
                if provider_results.get(name, {}).get("error")
            ]
            combined = "; ".join(messages) if messages else "All provider requests failed."

            if error_codes and all(code == "auth_failure" for code in error_codes):
                raise ThanosLocalAuthError(combined)
            if len(providers) == 1 and any(code == "invalid_args" for code in error_codes):
                raise ValueError(combined)
            raise ThanosLocalApiError(combined)

        return {
            "providers": provider_results,
            "summary": {
                "requested_provider": requested_provider,
                "queried": providers,
                "succeeded": succeeded,
                "failed": failed,
            },
        }

    def _require_github_org(self) -> str:
        org = (self.github_org or "").strip()
        if not org:
            raise ValueError("Missing GITHUB_ORG. Example: export GITHUB_ORG=<org>")
        return org

    def _get_github_token(self, *, force_refresh: bool = False) -> str:
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
            raise ThanosLocalAuthError(
                "Failed to acquire GitHub token. Set GITHUB_TOKEN or run `gh auth login`."
            ) from exc

        token = result.stdout.strip()
        if not token:
            raise ThanosLocalAuthError(
                "GitHub token is empty. Set GITHUB_TOKEN or run `gh auth login`."
            )

        self._github_token = token
        return self._github_token

    def _github_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expect_json: bool = True,
        session: requests.Session | None = None,
    ) -> Any:
        method_upper = method.upper()
        is_retryable_get = method_upper == "GET" and self._http_retry_max_attempts > 1
        url = path if path.startswith("http") else f"{self.github_api_url}{path}"
        request_headers = dict(headers or {})
        request_headers.setdefault("Accept", "application/vnd.github+json")
        request_headers.setdefault("X-GitHub-Api-Version", self.github_api_version)
        http_session = self._get_http_session(session=session)

        response: Any = None
        for retry_index in range(self._http_retry_max_attempts):
            first_attempt_headers = dict(request_headers)
            first_attempt_headers["Authorization"] = f"Bearer {self._get_github_token()}"
            try:
                response = http_session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=first_attempt_headers,
                    timeout=self.github_timeout_seconds,
                )
            except requests.RequestException as exc:
                if is_retryable_get and retry_index < self._http_retry_max_attempts - 1:
                    time.sleep(self._retry_sleep_seconds(response=None, retry_index=retry_index))
                    continue
                raise ThanosLocalApiError(f"Request error for {url}: {exc}") from exc

            if response.status_code in (401, 403):
                retry_headers = dict(request_headers)
                retry_headers["Authorization"] = f"Bearer {self._get_github_token(force_refresh=True)}"
                response = http_session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=retry_headers,
                    timeout=self.github_timeout_seconds,
                )

            if (
                is_retryable_get
                and self._is_retryable_get_status(int(response.status_code))
                and retry_index < self._http_retry_max_attempts - 1
            ):
                time.sleep(self._retry_sleep_seconds(response=response, retry_index=retry_index))
                continue
            break

        if response is None:
            raise ThanosLocalApiError(f"No response received for {url}")

        if response.status_code in (401, 403):
            raise ThanosLocalAuthError(
                f"GitHub authentication rejected with HTTP {response.status_code}. "
                "Set GITHUB_TOKEN or run `gh auth login` and retry."
            )

        if response.status_code == 429:
            raise ThanosLocalApiError(
                "GitHub API rate limited (HTTP 429). Narrow scope and retry.",
                status_code=response.status_code,
            )

        if not 200 <= response.status_code < 300:
            text = (response.text or "").strip()
            if len(text) > 500:
                text = text[:500] + "..."
            raise ThanosLocalApiError(
                f"HTTP {response.status_code} for {url}: {text}",
                status_code=response.status_code,
            )

        if not expect_json:
            return response.text

        if response.status_code == 204:
            return {}

        body = response.text or ""
        if not body.strip():
            return {}

        try:
            return response.json()
        except ValueError as exc:
            raise ThanosLocalApiError(
                f"Expected JSON response from {url} but received invalid JSON"
            ) from exc

    def _github_request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        session: requests.Session | None = None,
    ) -> dict[str, Any]:
        data = self._github_request(
            method,
            path,
            params=params,
            json_body=json_body,
            headers=headers,
            expect_json=True,
            session=session,
        )
        if isinstance(data, dict):
            return data
        raise ThanosLocalApiError(f"Expected dictionary response from {path}")

    def _github_request_text(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        session: requests.Session | None = None,
    ) -> str:
        data = self._github_request(
            method,
            path,
            params=params,
            headers=headers,
            expect_json=False,
            session=session,
        )
        return str(data)

    def _almsearch_url(self, suffix: str) -> str:
        return f"https://almsearch.dev.azure.com/{self.org_name}{suffix}"

    @staticmethod
    def _normalize_path(path: str | None) -> str:
        if not path:
            return "/"
        if path.startswith("/"):
            return path
        return "/" + path

    @staticmethod
    def _parse_iso_datetime(value: str | datetime | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    @staticmethod
    def _match_all_pattern(pattern: str) -> bool:
        return pattern in (".*", "^.*$", ".*$", "^.*")

    @staticmethod
    def _slice_lines(
        lines: list[str],
        *,
        from_line: int | None,
        to_line: int | None,
    ) -> list[str]:
        if from_line is None and to_line is None:
            return lines
        start_idx = (from_line - 1) if from_line and from_line > 0 else 0
        end_idx = to_line if to_line and to_line > 0 else len(lines)
        return lines[start_idx:end_idx]

    @staticmethod
    def _compile_search_pattern(
        pattern: str,
        *,
        case_insensitive: bool,
    ) -> tuple[re.Pattern[str] | None, str | None]:
        flags = re.IGNORECASE if case_insensitive else 0
        try:
            return re.compile(pattern, flags), None
        except re.error as exc:
            return None, f"Error: Invalid regex pattern - {exc}"

    @staticmethod
    def _parse_bool_env(name: str, *, default: bool) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        normalized = value.strip().lower()
        if not normalized:
            return default
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default

    @staticmethod
    def _parse_int_env(
        name: str,
        *,
        default: int,
        min_value: int,
        max_value: int,
    ) -> int:
        value = os.getenv(name)
        if value is None:
            return default
        try:
            parsed = int(value.strip())
        except (TypeError, ValueError):
            return default
        return max(min_value, min(max_value, parsed))

    def _configure_http_session(self, session: Any) -> None:
        if not hasattr(session, "mount"):
            return
        try:
            adapter = HTTPAdapter(
                pool_connections=self._http_pool_connections,
                pool_maxsize=self._http_pool_maxsize,
                max_retries=0,
            )
            session.mount("https://", adapter)
            session.mount("http://", adapter)
        except Exception:
            # Keep compatibility with mocked/injected session objects.
            return

    @staticmethod
    def _is_retryable_get_status(status_code: int) -> bool:
        return status_code in {429, 500, 502, 503, 504}

    @staticmethod
    def _parse_retry_after_seconds(response: Any) -> float | None:
        headers = getattr(response, "headers", {}) or {}
        raw = headers.get("Retry-After")
        if not raw:
            return None
        text = str(raw).strip()
        if not text:
            return None
        if text.isdigit():
            return max(0.0, float(text))
        try:
            retry_at = parsedate_to_datetime(text)
        except Exception:
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        return max(0.0, (retry_at - now).total_seconds())

    def _retry_sleep_seconds(self, *, response: Any, retry_index: int) -> float:
        retry_after_seconds = self._parse_retry_after_seconds(response)
        if retry_after_seconds is not None:
            return min(30.0, retry_after_seconds)
        return self._http_retry_backoff_seconds * (2**max(0, retry_index))

    def _get_http_session(self, *, session: requests.Session | None = None) -> requests.Session:
        if session is not None:
            return session
        if threading.current_thread() is threading.main_thread():
            return self._session
        worker_session = getattr(self._http_thread_local, "session", None)
        if isinstance(worker_session, requests.Session):
            return worker_session
        worker_session = requests.Session()
        self._configure_http_session(worker_session)
        self._http_thread_local.session = worker_session
        return worker_session

    def _github_get_thread_session(self) -> requests.Session:
        return self._get_http_session()

    def list_projects(self) -> list[dict[str, Any]]:
        url = f"{self.org_url}/_apis/projects"
        params = {"api-version": self.api_version}
        data = self._request_json("GET", url, params=params)
        values = data.get("value", [])
        return [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "state": item.get("state"),
                "url": item.get("url"),
            }
            for item in values
            if isinstance(item, dict)
        ]

    def list_repositories(self, project: str) -> list[dict[str, Any]]:
        url = f"{self.org_url}/{project}/_apis/git/repositories"
        params = {"api-version": self.api_version}
        data = self._request_json("GET", url, params=params)
        values = data.get("value", [])
        return [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "defaultBranch": item.get("defaultBranch"),
                "webUrl": item.get("webUrl"),
            }
            for item in values
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
        if repos and not project:
            raise ValueError("Repository filter requires --project")

        filters: dict[str, Any] = {}
        if project:
            filters["Project"] = [project]
        if repos:
            filters["Repository"] = repos

        payload: dict[str, Any] = {
            "searchText": query,
            "$skip": max(0, skip),
            "$top": max(1, take),
            "filters": filters,
            "$orderBy": [{"field": "filename", "sortOrder": "ASC"}],
        }

        url = self._almsearch_url(f"/_apis/search/codesearchresults?api-version={self.api_version}")
        data = self._request_json(
            "POST",
            url,
            json_body=payload,
            headers={"Content-Type": "application/json"},
        )

        results = []
        for item in data.get("results", []):
            if not isinstance(item, dict):
                continue
            project_name = (item.get("project") or {}).get("name", "")
            repo_name = (item.get("repository") or {}).get("name", "")
            path = item.get("path", "")
            results.append(f"{project_name}/{repo_name}:{path}")

        return {
            "matchesCount": data.get("count", len(results)),
            "results": results,
        }

    def _get_repository_files(
        self,
        *,
        project: str,
        repo: str,
        path: str,
        branch: str | None,
    ) -> list[dict[str, Any]]:
        url = f"{self.org_url}/{project}/_apis/git/repositories/{repo}/items"
        params: dict[str, Any] = {
            "scopePath": path,
            "recursionLevel": "Full",
            "includeContentMetadata": "true",
            "api-version": self.api_version,
        }

        if branch:
            params["versionDescriptor.version"] = branch
            params["versionDescriptor.versionType"] = "branch"

        data = self._request_json("GET", url, params=params)
        values = data.get("value", [])
        files: list[dict[str, Any]] = []
        for item in values:
            if not isinstance(item, dict):
                continue
            if item.get("gitObjectType") != "blob":
                continue
            files.append(item)
        return files

    def _get_file_text(
        self,
        *,
        project: str,
        repo: str,
        file_path: str,
        branch: str | None,
    ) -> str:
        url = f"{self.org_url}/{project}/_apis/git/repositories/{repo}/items"
        params: dict[str, Any] = {
            "path": file_path,
            "includeContent": "true",
            "api-version": self.api_version,
        }
        if branch:
            params["versionDescriptor.version"] = branch
            params["versionDescriptor.versionType"] = "branch"

        data = self._request_json("GET", url, params=params)
        content = data.get("content")
        if isinstance(content, str):
            return content

        params["$format"] = "text"
        return self._request_text("GET", url, params=params)

    def grep(
        self,
        *,
        project: str,
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
        folder_path = self._normalize_path(path)
        normalized_branch = normalize_branch_name(branch)
        regex_pattern = pattern or ".*"
        is_match_all = self._match_all_pattern(regex_pattern)

        file_regex = glob_to_regex(glob) if glob else ".*"
        filename_filter = re.compile(file_regex)

        files = self._get_repository_files(
            project=project,
            repo=repo,
            path=folder_path,
            branch=normalized_branch,
        )

        matching = [
            {
                "path": file_item.get("path", ""),
                "is_binary": bool(
                    file_item.get("isBinary")
                    or (file_item.get("contentMetadata") or {}).get("isBinary")
                ),
            }
            for file_item in files
            if filename_filter.search(os.path.basename(str(file_item.get("path", ""))))
        ]

        if output_mode == "files_with_matches" and is_match_all:
            text = "\n".join(item["path"] for item in matching)
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

        search_pattern, compile_error = self._compile_search_pattern(
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

        for file_item in matching:
            file_path = file_item["path"]
            if file_item["is_binary"]:
                continue

            try:
                content = self._get_file_text(
                    project=project,
                    repo=repo,
                    file_path=file_path,
                    branch=normalized_branch,
                )
            except Exception as exc:
                warnings.append(f"failed to read {file_path}: {exc}")
                continue

            lines = self._slice_lines(
                content.splitlines(),
                from_line=from_line,
                to_line=to_line,
            )

            match_line_nums = {
                idx for idx, line in enumerate(lines) if search_pattern.search(line)
            }
            if not match_line_nums:
                continue

            files_matched += 1
            if output_mode == "files_with_matches":
                output_lines.append(file_path)
                continue
            if output_mode == "count":
                output_lines.append(f"{file_path}:{len(match_line_nums)}")
                continue

            formatted = format_grep_matches(
                file_path,
                lines,
                match_line_nums,
                context_lines or 0,
                include_line_numbers=True,
            )
            output_lines.extend(formatted)

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

    def list_pull_requests(
        self,
        *,
        projects: list[str] | None = None,
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
        normalized_status = []
        for status in effective_status:
            lowered = status.strip().lower()
            if lowered not in allowed_status:
                raise ValueError("status must be one of: active, completed, abandoned")
            if lowered not in normalized_status:
                normalized_status.append(lowered)

        if projects:
            project_names = projects
        else:
            project_names = [entry["name"] for entry in self.list_projects() if entry.get("name")]

        repo_targets = [repo for repo in repos or [] if repo]
        repo_filter = {repo.lower() for repo in repo_targets}
        creator_filter = [creator.lower() for creator in creators or []]

        from_dt = self._parse_iso_datetime(date_from)
        to_dt = self._parse_iso_datetime(date_to)

        results: list[dict[str, Any]] = []

        for project_name in project_names:
            repo_scopes = repo_targets or [None]
            for repo_scope in repo_scopes:
                for status in normalized_status:
                    page_size = min(max(take, 1), 100)
                    local_skip = 0
                    if repo_scope:
                        url = (
                            f"{self.org_url}/{project_name}/_apis/git/repositories/"
                            f"{quote(str(repo_scope), safe='')}/pullrequests"
                        )
                    else:
                        url = f"{self.org_url}/{project_name}/_apis/git/pullrequests"
                    while True:
                        params: dict[str, Any] = {
                            "api-version": self.api_version,
                            "searchCriteria.status": status,
                            "$top": page_size,
                            "$skip": local_skip,
                        }
                        if include_labels:
                            params["searchCriteria.includeLabels"] = "true"
                        params["searchCriteria.queryTimeRangeType"] = (
                            "closed" if status in {"completed", "abandoned"} else "created"
                        )
                        if from_dt:
                            params["searchCriteria.minTime"] = from_dt.astimezone(UTC).strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            )
                        if to_dt:
                            params["searchCriteria.maxTime"] = to_dt.astimezone(UTC).strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            )

                        data = self._request_json("GET", url, params=params)
                        items = data.get("value", [])
                        if not isinstance(items, list) or not items:
                            break

                        for item in items:
                            if not isinstance(item, dict):
                                continue

                            if exclude_drafts and item.get("isDraft"):
                                continue

                            repository = item.get("repository") or {}
                            repository_name = str(repository.get("name") or "")
                            repository_id = str(repository.get("id") or "")
                            if repo_scope is None and repo_filter:
                                if (
                                    repository_name.lower() not in repo_filter
                                    and repository_id.lower() not in repo_filter
                                ):
                                    continue

                            created_by = item.get("createdBy") or {}
                            creator_display = str(created_by.get("displayName") or "")
                            creator_unique = str(created_by.get("uniqueName") or "")
                            if creator_filter:
                                source = f"{creator_display} {creator_unique}".lower()
                                if not any(token in source for token in creator_filter):
                                    continue

                            creation_dt = self._parse_iso_datetime(item.get("creationDate"))
                            closed_dt = self._parse_iso_datetime(item.get("closedDate"))
                            ref_dt = closed_dt if status in {"completed", "abandoned"} else creation_dt

                            if from_dt and ref_dt and ref_dt < from_dt:
                                continue
                            if to_dt and ref_dt and ref_dt > to_dt:
                                continue

                            labels: list[str] = []
                            if include_labels:
                                for label in item.get("labels") or []:
                                    if isinstance(label, dict):
                                        name = str(label.get("name") or "").strip()
                                        if name:
                                            labels.append(name)

                            results.append(
                                {
                                    "pr_id": item.get("pullRequestId"),
                                    "title": item.get("title") or "",
                                    "created_by": creator_display or creator_unique,
                                    "status": status,
                                    "creation_date": item.get("creationDate"),
                                    "project_name": project_name,
                                    "repository_name": repository_name,
                                    "repository_id": repository_id,
                                    "closed_date": (
                                        closed_dt.astimezone(UTC).strftime("%Y-%m-%d")
                                        if closed_dt
                                        else None
                                    ),
                                    "source_branch": normalize_branch_name(item.get("sourceRefName")),
                                    "target_branch": normalize_branch_name(item.get("targetRefName")),
                                    "target_ref": item.get("targetRefName"),
                                    "labels": labels,
                                }
                            )

                        if len(items) < page_size:
                            break
                        local_skip += page_size

        total = len(results)
        results.sort(key=lambda row: str(row.get("creation_date") or ""), reverse=True)
        paged = results[max(0, skip) : max(0, skip) + max(1, take)]
        has_more = total > max(0, skip) + len(paged)

        return {
            "returned_count": len(paged),
            "has_more": has_more,
            "results": paged,
        }

    def get_pull_request(self, *, project: str, repo: str, pull_request_id: int) -> dict[str, Any]:
        pr_url = (
            f"{self.org_url}/{project}/_apis/git/repositories/{repo}/pullrequests/"
            f"{pull_request_id}"
        )
        threads_url = (
            f"{self.org_url}/{project}/_apis/git/repositories/{repo}/pullrequests/"
            f"{pull_request_id}/threads"
        )
        iterations_url = (
            f"{self.org_url}/{project}/_apis/git/repositories/{repo}/pullrequests/"
            f"{pull_request_id}/iterations"
        )

        params = {"api-version": self.api_version}
        pr = self._request_json("GET", pr_url, params=params)
        threads_response = self._request_json("GET", threads_url, params=params)
        threads = threads_response.get("value", []) if isinstance(threads_response, dict) else []

        changed_files: list[str] = []
        try:
            iterations = self._request_json("GET", iterations_url, params=params).get("value", [])
            if iterations:
                latest_iteration = max(
                    [it.get("id", 0) for it in iterations if isinstance(it, dict)],
                    default=0,
                )
                if latest_iteration:
                    changes_url = (
                        f"{self.org_url}/{project}/_apis/git/repositories/{repo}/pullrequests/"
                        f"{pull_request_id}/iterations/{latest_iteration}/changes"
                    )
                    changes = self._request_json(
                        "GET",
                        changes_url,
                        params={"api-version": self.api_version, "$top": 2000},
                    )
                    for entry in changes.get("changeEntries", []):
                        if not isinstance(entry, dict):
                            continue
                        item = entry.get("item") or {}
                        path = item.get("path")
                        if path:
                            changed_files.append(path)
        except Exception:
            changed_files = []

        return {
            "pull_request": pr,
            "threads": threads,
            "changed_files": changed_files,
        }

    def get_pull_request_threads(
        self,
        *,
        project: str,
        repo: str,
        pull_request_id: int,
        include_deleted: bool = False,
    ) -> dict[str, Any]:
        threads_url = (
            f"{self.org_url}/{project}/_apis/git/repositories/{repo}/pullrequests/"
            f"{pull_request_id}/threads"
        )
        params = {"api-version": self.api_version}
        threads_response = self._request_json("GET", threads_url, params=params)
        raw_threads = threads_response.get("value", []) if isinstance(threads_response, dict) else []

        threads: list[dict[str, Any]] = []
        total_comments = 0

        for entry in raw_threads:
            if not isinstance(entry, dict):
                continue

            if bool(entry.get("isDeleted")) and not include_deleted:
                continue

            thread_context = entry.get("threadContext") or {}
            right_file_start = thread_context.get("rightFileStart") or {}
            right_file_end = thread_context.get("rightFileEnd") or {}

            comments: list[dict[str, Any]] = []
            for comment_entry in entry.get("comments", []):
                if not isinstance(comment_entry, dict):
                    continue
                if bool(comment_entry.get("isDeleted")) and not include_deleted:
                    continue

                author = comment_entry.get("author") or {}
                comments.append(
                    {
                        "id": comment_entry.get("id"),
                        "author": str(author.get("displayName") or author.get("uniqueName") or ""),
                        "content": str(comment_entry.get("content") or ""),
                        "comment_type": comment_entry.get("commentType"),
                        "is_deleted": bool(comment_entry.get("isDeleted")),
                        "published_date": comment_entry.get("publishedDate"),
                        "last_updated_date": comment_entry.get("lastUpdatedDate"),
                    }
                )

            total_comments += len(comments)
            threads.append(
                {
                    "id": entry.get("id"),
                    "status": entry.get("status"),
                    "is_deleted": bool(entry.get("isDeleted")),
                    "file_path": thread_context.get("filePath"),
                    "line_start": right_file_start.get("line"),
                    "line_end": right_file_end.get("line"),
                    "comments": comments,
                    "comment_count": len(comments),
                    "published_date": entry.get("publishedDate"),
                    "last_updated_date": entry.get("lastUpdatedDate"),
                }
            )

        return {
            "pull_request_id": pull_request_id,
            "project_name": project,
            "repository_name": repo,
            "returned_count": len(threads),
            "total_comments": total_comments,
            "threads": threads,
        }

    def get_build_log(self, *, project: str, build_id: int) -> dict[str, Any]:
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
        self,
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
        self,
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
    ) -> dict[str, Any]:
        regex_pattern = pattern or ".*"
        is_match_all = self._match_all_pattern(regex_pattern)

        if log_id is None and is_match_all:
            return {
                "text": (
                    "Error: Specify a log_id to read full content, or provide a pattern to search across all logs."
                ),
                "logs_matched": 0,
                "warnings": [],
                "partial": False,
            }

        search_pattern, compile_error = self._compile_search_pattern(
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

        warnings: list[str] = []

        if log_id is not None and is_match_all:
            if output_mode == "logs_with_matches":
                text = f"Log {log_id}"
                return {
                    "text": text,
                    "logs_matched": 1,
                    "warnings": [],
                    "partial": False,
                }
            if output_mode == "count":
                content = self.get_build_log_content(
                    project=project,
                    build_id=build_id,
                    log_id=log_id,
                    start_line=from_line,
                    end_line=to_line,
                )
                return {
                    "text": f"Log {log_id}:{len(content.splitlines())}",
                    "logs_matched": 1,
                    "warnings": [],
                    "partial": False,
                }

            content = self.get_build_log_content(
                project=project,
                build_id=build_id,
                log_id=log_id,
                start_line=from_line,
                end_line=to_line,
            )
            start = from_line or 1
            text = "\n".join(
                f"{start + idx}:{line}" for idx, line in enumerate(content.splitlines())
            )
            text = truncate_output(
                text,
                self.max_output_chars,
                "Use from_line/to_line to read specific ranges.",
            )
            return {
                "text": text,
                "logs_matched": 1,
                "warnings": [],
                "partial": False,
            }

        if log_id is not None:
            log_ids = [log_id]
        else:
            build_logs = self.get_build_log(project=project, build_id=build_id)
            log_ids = [
                int(entry["id"])
                for entry in build_logs.get("logs", [])
                if isinstance(entry, dict) and entry.get("id") is not None
            ]

        output_lines: list[str] = []
        logs_matched = 0

        for current_log_id in log_ids:
            try:
                content = self.get_build_log_content(
                    project=project,
                    build_id=build_id,
                    log_id=current_log_id,
                )
            except Exception as exc:
                warnings.append(f"failed to read Log {current_log_id}: {exc}")
                continue

            lines = self._slice_lines(
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

    def get_ticket_by_id(self, *, project: str, work_item_id: int) -> dict[str, Any]:
        url = f"{self.org_url}/{project}/_apis/wit/workitems/{work_item_id}"
        params = {"$expand": "relations", "api-version": self.api_version}
        return self._request_json("GET", url, params=params)

    def list_work_items(
        self,
        *,
        project: str,
        wiql: str,
        skip: int = 0,
        take: int = 20,
    ) -> dict[str, Any]:
        wiql_url = f"{self.org_url}/{project}/_apis/wit/wiql"
        wiql_data = self._request_json(
            "POST",
            wiql_url,
            params={"api-version": self.api_version},
            json_body={"query": wiql},
            headers={"Content-Type": "application/json"},
        )

        ids = [
            int(item["id"])
            for item in wiql_data.get("workItems", [])
            if isinstance(item, dict) and item.get("id") is not None
        ]

        total = len(ids)
        paged_ids = ids[max(0, skip) : max(0, skip) + max(1, take)]
        has_more = total > max(0, skip) + len(paged_ids)

        if not paged_ids:
            return {
                "total_count": total,
                "returned_count": 0,
                "has_more": has_more,
                "results": [],
            }

        results: list[dict[str, Any]] = []
        details_url = f"{self.org_url}/{project}/_apis/wit/workitems"
        for start in range(0, len(paged_ids), 200):
            chunk = paged_ids[start : start + 200]
            data = self._request_json(
                "GET",
                details_url,
                params={
                    "ids": ",".join(str(work_item_id) for work_item_id in chunk),
                    "$expand": "relations",
                    "api-version": self.api_version,
                },
            )
            for item in data.get("value", []):
                if isinstance(item, dict):
                    results.append(item)

        return {
            "total_count": total,
            "returned_count": len(results),
            "has_more": has_more,
            "results": results,
        }

    def search_work_items(
        self,
        *,
        query: str,
        project: str | None = None,
        area: str | None = None,
        work_item_type: str | None = None,
        state: str | None = None,
        assigned_to: str | None = None,
        skip: int = 0,
        take: int = 20,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "searchText": query,
            "$skip": max(0, skip),
            "$top": max(1, min(200, take)),
        }

        filters: dict[str, list[str]] = {}
        if project:
            filters["System.TeamProject"] = [project]
        if area:
            filters["System.AreaPath"] = [area]
        if work_item_type:
            filters["System.WorkItemType"] = [work_item_type]
        if state:
            filters["System.State"] = [state]
        if assigned_to:
            filters["System.AssignedTo"] = [assigned_to]

        if filters:
            payload["filters"] = filters

        url = self._almsearch_url(f"/_apis/search/workitemsearchresults?api-version={self.api_version}")
        data = self._request_json(
            "POST",
            url,
            json_body=payload,
            headers={"Content-Type": "application/json"},
        )

        results = []
        for item in data.get("results", []):
            if not isinstance(item, dict):
                continue
            fields = item.get("fields") or {}
            assigned = fields.get("System.AssignedTo")
            if isinstance(assigned, dict):
                assigned_name = assigned.get("displayName") or assigned.get("uniqueName")
            else:
                assigned_name = assigned

            tags_value = fields.get("System.Tags")
            tags: list[str] = []
            if isinstance(tags_value, str):
                tags = [tag.strip() for tag in tags_value.split(";") if tag.strip()]

            highlights: list[str] = []
            for match in item.get("matches") or item.get("hits") or []:
                if isinstance(match, dict):
                    value = (
                        match.get("value")
                        or match.get("snippet")
                        or match.get("text")
                        or match.get("highlights")
                    )
                    if isinstance(value, list):
                        highlights.extend(str(entry) for entry in value if entry)
                    elif value:
                        highlights.append(str(value))
                elif isinstance(match, str) and match:
                    highlights.append(match)

            results.append(
                {
                    "id": fields.get("System.Id"),
                    "title": fields.get("System.Title"),
                    "state": fields.get("System.State"),
                    "type": fields.get("System.WorkItemType"),
                    "project": fields.get("System.TeamProject"),
                    "assigned_to": assigned_name,
                    "tags": tags,
                    "created_date": fields.get("System.CreatedDate"),
                    "changed_date": fields.get("System.ChangedDate"),
                    "url": item.get("url"),
                    "highlights": highlights,
                }
            )

        matches_count = data.get("count", len(results))
        return {
            "matchesCount": matches_count,
            "returned_count": len(results),
            "has_more": matches_count > max(0, skip) + len(results),
            "results": results,
        }

    def get_my_work_items(
        self,
        *,
        project: str | None = None,
        include_closed: bool = False,
        skip: int = 0,
        take: int = 20,
    ) -> dict[str, Any]:
        state_filter = ""
        if not include_closed:
            state_filter = " AND [System.State] NOT IN ('Closed', 'Done', 'Removed')"

        if project:
            wiql = (
                "SELECT [System.Id], [System.Title], [System.State], [System.WorkItemType] "
                "FROM WorkItems "
                f"WHERE [System.TeamProject] = '{project}' "
                "AND [System.AssignedTo] = @Me"
                f"{state_filter} "
                "ORDER BY [System.ChangedDate] DESC"
            )
            return self.list_work_items(project=project, wiql=wiql, skip=skip, take=take)

        aggregated: list[dict[str, Any]] = []
        warnings: list[str] = []
        for project_entry in self.list_projects():
            project_name = project_entry.get("name")
            if not project_name:
                continue
            wiql = (
                "SELECT [System.Id], [System.Title], [System.State], [System.WorkItemType] "
                "FROM WorkItems "
                "WHERE [System.AssignedTo] = @Me"
                f"{state_filter} "
                "ORDER BY [System.ChangedDate] DESC"
            )
            try:
                result = self.list_work_items(
                    project=str(project_name),
                    wiql=wiql,
                    skip=0,
                    take=max(1, take),
                )
            except Exception as exc:
                warnings.append(f"{project_name}: {exc}")
                continue
            aggregated.extend(result.get("results", []))

        aggregated = aggregated[max(0, skip) : max(0, skip) + max(1, take)]
        return {
            "returned_count": len(aggregated),
            "has_more": False,
            "results": aggregated,
            "warnings": warnings,
        }

    def _github_repo_prefix(self, repo: str) -> str:
        org = self._require_github_org()
        return f"/repos/{quote(org, safe='')}/{quote(repo, safe='')}"

    def _github_get_paginated_list(
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
            data = self._github_request("GET", path, params=query, expect_json=True)
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

    def _github_get_repository_default_branch(self, repo: str) -> str:
        cache_key = repo.strip().lower()
        cached = self._github_default_branch_cache.get(cache_key)
        if cached:
            return cached

        data = self._github_request_json("GET", f"{self._github_repo_prefix(repo)}")
        branch = str(data.get("default_branch") or "").strip() or "main"
        self._github_default_branch_cache[cache_key] = branch
        return branch

    def _github_list_projects(self) -> list[dict[str, Any]]:
        org = self._require_github_org()
        return [
            {
                "id": org,
                "name": org,
                "state": "active",
                "url": f"https://github.com/{org}",
            }
        ]

    def _github_list_repositories(self) -> list[dict[str, Any]]:
        org = self._require_github_org()
        repos = self._github_get_paginated_list(f"/orgs/{quote(org, safe='')}/repos")
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

    def _github_search_code(
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

        search_targets = effective_repos or [None]
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
                data = self._github_request_json(
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

    def _github_get_repository_files(
        self,
        *,
        repo: str,
        path: str | None,
        branch: str | None,
    ) -> list[dict[str, Any]]:
        normalized_path = self._normalize_path(path)
        ref = normalize_branch_name(branch) or self._github_get_repository_default_branch(repo)
        prefix = normalized_path.strip("/")
        repo_prefix = self._github_repo_prefix(repo)

        def _tree_entries(treeish: str, *, recursive: bool) -> list[dict[str, Any]]:
            encoded_treeish = quote(treeish, safe="")
            params = {"recursive": "1"} if recursive else None
            data = self._github_request_json(
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
                output: list[dict[str, Any]] = []
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

    def _github_get_file_text(
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
                blob_data = self._github_request_json(
                    "GET",
                    f"{self._github_repo_prefix(repo)}/git/blobs/{quote(blob_sha, safe='')}",
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
                # Fall back to path/ref retrieval when blob endpoint is unavailable for this object.
                pass

        ref = normalize_branch_name(branch) or self._github_get_repository_default_branch(repo)
        encoded_path = quote(file_path.lstrip("/"), safe="/")
        data = self._github_request_json(
            "GET",
            f"{self._github_repo_prefix(repo)}/contents/{encoded_path}",
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
        return self._github_request_text(
            "GET",
            f"{self._github_repo_prefix(repo)}/contents/{encoded_path}",
            params={"ref": ref},
            session=session,
        )

    @staticmethod
    def _github_default_grep_workers(candidate_files: int) -> int:
        if candidate_files <= 1:
            return 1
        if candidate_files <= 8:
            return 4
        if candidate_files <= 32:
            return 8
        if candidate_files <= 96:
            return 16
        return 20

    def _github_grep(
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
        is_match_all = self._match_all_pattern(regex_pattern)
        file_regex = glob_to_regex(glob) if glob else ".*"
        filename_filter = re.compile(file_regex)
        resolved_branch = normalize_branch_name(branch) or self._github_get_repository_default_branch(repo)
        files = self._github_get_repository_files(repo=repo, path=path, branch=resolved_branch)
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

        search_pattern, compile_error = self._compile_search_pattern(
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

        grep_parallel_enabled = self._parse_bool_env(
            "GITHUB_GREP_ENABLE_PARALLEL",
            default=True,
        )
        grep_max_workers = self._parse_int_env(
            "GITHUB_GREP_MAX_WORKERS",
            default=self._github_default_grep_workers(len(matching)),
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
                content = self._github_get_file_text(
                    repo=repo,
                    file_path=file_path,
                    branch=resolved_branch,
                    blob_sha=blob_sha,
                    session=session,
                )
            except Exception as exc:
                return [], 0, f"failed to read {file_path}: {exc}"

            lines = self._slice_lines(
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
                session=self._github_get_thread_session(),
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
                    except Exception as exc:  # pragma: no cover - safety net
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
    def _github_pr_status(pr: dict[str, Any]) -> str:
        state = str(pr.get("state") or "").lower()
        if state == "open":
            return "active"
        if pr.get("merged_at"):
            return "completed"
        return "abandoned"

    def _github_list_pull_requests(
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
        normalized_status = []
        for status in effective_status:
            lowered = status.strip().lower()
            if lowered not in allowed_status:
                raise ValueError("status must be one of: active, completed, abandoned")
            if lowered not in normalized_status:
                normalized_status.append(lowered)

        repo_names = [item for item in (repos or []) if item]
        if not repo_names:
            repo_names = [entry["name"] for entry in self._github_list_repositories() if entry.get("name")]

        creator_filter = [item.lower() for item in creators or []]
        from_dt = self._parse_iso_datetime(date_from)
        to_dt = self._parse_iso_datetime(date_to)
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
                    pulls_data = self._github_request(
                        "GET",
                        f"{self._github_repo_prefix(repo_name)}/pulls",
                        params={"state": github_state, "per_page": per_page, "page": page},
                        expect_json=True,
                    )
                    if not isinstance(pulls_data, list):
                        break
                    pulls = [item for item in pulls_data if isinstance(item, dict)]
                    if not pulls:
                        break

                    for item in pulls:
                        status = self._github_pr_status(item)
                        if status not in normalized_status:
                            continue
                        if exclude_drafts and bool(item.get("draft")):
                            continue

                        user = item.get("user") or {}
                        creator = str(user.get("login") or "")
                        if creator_filter and not any(token in creator.lower() for token in creator_filter):
                            continue

                        created_dt = self._parse_iso_datetime(item.get("created_at"))
                        closed_dt = self._parse_iso_datetime(item.get("closed_at"))
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

    def _github_get_pull_request(self, *, repo: str, pull_request_id: int) -> dict[str, Any]:
        pr = self._github_request_json("GET", f"{self._github_repo_prefix(repo)}/pulls/{pull_request_id}")
        files = self._github_get_paginated_list(
            f"{self._github_repo_prefix(repo)}/pulls/{pull_request_id}/files",
            limit=2000,
        )
        changed_files = [str(item.get("filename")) for item in files if item.get("filename")]
        threads_data = self._github_get_pull_request_threads(repo=repo, pull_request_id=pull_request_id)
        mapped_pr = {
            "pullRequestId": pr.get("number"),
            "title": pr.get("title"),
            "status": self._github_pr_status(pr),
            "createdBy": {"displayName": (pr.get("user") or {}).get("login", "")},
            "sourceRefName": f"refs/heads/{(pr.get('head') or {}).get('ref', '')}",
            "targetRefName": f"refs/heads/{(pr.get('base') or {}).get('ref', '')}",
        }
        return {
            "pull_request": mapped_pr,
            "threads": threads_data.get("threads", []),
            "changed_files": changed_files,
        }

    def _github_get_pull_request_threads(
        self,
        *,
        repo: str,
        pull_request_id: int,
        include_deleted: bool = False,
    ) -> dict[str, Any]:
        issue_comments = self._github_get_paginated_list(
            f"{self._github_repo_prefix(repo)}/issues/{pull_request_id}/comments"
        )
        review_comments = self._github_get_paginated_list(
            f"{self._github_repo_prefix(repo)}/pulls/{pull_request_id}/comments"
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

    def _github_get_build_log(self, *, repo: str, build_id: int) -> dict[str, Any]:
        run = self._github_request_json("GET", f"{self._github_repo_prefix(repo)}/actions/runs/{build_id}")
        jobs_data = self._github_request_json(
            "GET",
            f"{self._github_repo_prefix(repo)}/actions/runs/{build_id}/jobs",
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

    def _github_get_build_log_content(
        self,
        *,
        repo: str,
        log_id: int,
    ) -> str:
        return self._github_request_text(
            "GET",
            f"{self._github_repo_prefix(repo)}/actions/jobs/{log_id}/logs",
            headers={"Accept": "application/vnd.github+json"},
        )

    def _github_grep_build_log(
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
        is_match_all = self._match_all_pattern(regex_pattern)
        if log_id is None and is_match_all:
            return {
                "text": (
                    "Error: Specify a log_id to read full content, or provide a pattern to search across all logs."
                ),
                "logs_matched": 0,
                "warnings": [],
                "partial": False,
            }

        search_pattern, compile_error = self._compile_search_pattern(
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
            content = self._github_get_build_log_content(repo=repo, log_id=log_id)
            lines = self._slice_lines(
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
            build_logs = self._github_get_build_log(repo=repo, build_id=build_id)
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
                content = self._github_get_build_log_content(repo=repo, log_id=current_log_id)
            except Exception as exc:
                warnings.append(f"failed to read Log {current_log_id}: {exc}")
                continue

            lines = self._slice_lines(
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

    def _github_issue_to_work_item(self, issue: dict[str, Any], repo: str) -> dict[str, Any]:
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

    def _github_get_ticket_by_id(self, *, repo: str, work_item_id: int) -> dict[str, Any]:
        issue = self._github_request_json("GET", f"{self._github_repo_prefix(repo)}/issues/{work_item_id}")
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

    def _github_search_work_items(
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
        data = self._github_request_json(
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
            results.append(self._github_issue_to_work_item(issue, repo_from_url))

        total = int(data.get("total_count", len(results)))
        return {
            "matchesCount": total,
            "returned_count": len(results),
            "has_more": total > max(0, skip) + len(results),
            "results": results,
        }

    def _github_get_my_work_items(
        self,
        *,
        project: str | None = None,
        repo: str | None = None,
        include_closed: bool = False,
        skip: int = 0,
        take: int = 20,
    ) -> dict[str, Any]:
        result = self._github_search_work_items(
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

    def execute_projects_list(self, *, provider: str) -> dict[str, Any]:
        single_provider = self._normalize_single_provider(provider, command="projects.list")
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self.list_projects(),
                "github": lambda: self._github_list_projects(),
            },
        )

    def execute_repos_list(
        self,
        *,
        provider: str,
        project: str | None,
    ) -> dict[str, Any]:
        single_provider = self._normalize_single_provider(provider, command="repos.list")
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self.list_repositories(project=str(project)),
                "github": self._github_list_repositories,
            },
        )

    def execute_code_search(
        self,
        *,
        provider: str,
        query: str,
        project: str | None,
        repos: list[str] | None,
        skip: int,
        take: int,
    ) -> dict[str, Any]:
        return self._fanout(
            provider=provider,
            operations={
                "azdo": lambda: self.search_code(
                    query=query,
                    project=project,
                    repos=repos,
                    skip=skip,
                    take=take,
                ),
                "github": lambda: self._github_search_code(
                    query=query,
                    project=project,
                    repos=repos,
                    skip=skip,
                    take=take,
                ),
            },
        )

    def execute_code_grep(
        self,
        *,
        provider: str,
        project: str | None,
        repo: str,
        pattern: str | None,
        path: str | None,
        branch: str | None,
        glob: str | None,
        output_mode: Literal["content", "files_with_matches", "count"],
        case_insensitive: bool,
        context_lines: int | None,
        from_line: int | None,
        to_line: int | None,
    ) -> dict[str, Any]:
        single_provider = self._normalize_single_provider(provider, command="code.grep")
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self.grep(
                    project=str(project),
                    repo=repo,
                    pattern=pattern,
                    path=path,
                    branch=branch,
                    glob=glob,
                    output_mode=output_mode,
                    case_insensitive=case_insensitive,
                    context_lines=context_lines,
                    from_line=from_line,
                    to_line=to_line,
                ),
                "github": lambda: self._github_grep(
                    repo=repo,
                    pattern=pattern,
                    path=path,
                    branch=branch,
                    glob=glob,
                    output_mode=output_mode,
                    case_insensitive=case_insensitive,
                    context_lines=context_lines,
                    from_line=from_line,
                    to_line=to_line,
                ),
            },
        )

    def execute_pr_list(
        self,
        *,
        provider: str,
        projects: list[str] | None,
        repos: list[str] | None,
        statuses: list[str] | None,
        creators: list[str] | None,
        date_from: str | datetime | None,
        date_to: str | datetime | None,
        skip: int,
        take: int,
        exclude_drafts: bool,
        include_labels: bool,
    ) -> dict[str, Any]:
        single_provider = self._normalize_single_provider(provider, command="pr.list")
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self.list_pull_requests(
                    projects=projects,
                    repos=repos,
                    statuses=statuses,
                    creators=creators,
                    date_from=date_from,
                    date_to=date_to,
                    skip=skip,
                    take=take,
                    exclude_drafts=exclude_drafts,
                    include_labels=include_labels,
                ),
                "github": lambda: self._github_list_pull_requests(
                    repos=repos or projects,
                    statuses=statuses,
                    creators=creators,
                    date_from=date_from,
                    date_to=date_to,
                    skip=skip,
                    take=take,
                    exclude_drafts=exclude_drafts,
                    include_labels=include_labels,
                ),
            },
        )

    def execute_pr_get(
        self,
        *,
        provider: str,
        project: str | None,
        repo: str,
        pull_request_id: int,
    ) -> dict[str, Any]:
        single_provider = self._normalize_single_provider(provider, command="pr.get")
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self.get_pull_request(
                    project=str(project),
                    repo=repo,
                    pull_request_id=pull_request_id,
                ),
                "github": lambda: self._github_get_pull_request(
                    repo=repo,
                    pull_request_id=pull_request_id,
                ),
            },
        )

    def execute_pr_threads(
        self,
        *,
        provider: str,
        project: str | None,
        repo: str,
        pull_request_id: int,
    ) -> dict[str, Any]:
        single_provider = self._normalize_single_provider(provider, command="pr.threads")
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self.get_pull_request_threads(
                    project=str(project),
                    repo=repo,
                    pull_request_id=pull_request_id,
                ),
                "github": lambda: self._github_get_pull_request_threads(
                    repo=repo,
                    pull_request_id=pull_request_id,
                ),
            },
        )

    def execute_build_logs(
        self,
        *,
        provider: str,
        project: str | None,
        repo: str | None,
        build_id: int,
    ) -> dict[str, Any]:
        single_provider = self._normalize_single_provider(provider, command="build.logs")
        effective_repo = repo or project
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self.get_build_log(project=str(project), build_id=build_id),
                "github": lambda: self._github_get_build_log(repo=str(effective_repo), build_id=build_id),
            },
        )

    def execute_build_grep(
        self,
        *,
        provider: str,
        project: str | None,
        repo: str | None,
        build_id: int,
        log_id: int | None,
        pattern: str | None,
        output_mode: Literal["content", "logs_with_matches", "count"],
        case_insensitive: bool,
        context_lines: int | None,
        from_line: int | None,
        to_line: int | None,
    ) -> dict[str, Any]:
        single_provider = self._normalize_single_provider(provider, command="build.grep")
        effective_repo = repo or project
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self.grep_build_log(
                    project=str(project),
                    build_id=build_id,
                    log_id=log_id,
                    pattern=pattern,
                    output_mode=output_mode,
                    case_insensitive=case_insensitive,
                    context_lines=context_lines,
                    from_line=from_line,
                    to_line=to_line,
                ),
                "github": lambda: self._github_grep_build_log(
                    repo=str(effective_repo),
                    build_id=build_id,
                    log_id=log_id,
                    pattern=pattern,
                    output_mode=output_mode,
                    case_insensitive=case_insensitive,
                    context_lines=context_lines,
                    from_line=from_line,
                    to_line=to_line,
                ),
            },
        )

    def execute_board_ticket(
        self,
        *,
        provider: str,
        project: str | None,
        repo: str | None,
        work_item_id: int,
    ) -> dict[str, Any]:
        single_provider = self._normalize_single_provider(provider, command="board.ticket")
        effective_repo = repo or project
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self.get_ticket_by_id(project=str(project), work_item_id=work_item_id),
                "github": lambda: self._github_get_ticket_by_id(repo=str(effective_repo), work_item_id=work_item_id),
            },
        )

    def execute_board_list(
        self,
        *,
        provider: str,
        project: str | None,
        wiql: str,
        skip: int,
        take: int,
    ) -> dict[str, Any]:
        single_provider = self._normalize_single_provider(provider, command="board.list")
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self.list_work_items(
                    project=str(project),
                    wiql=wiql,
                    skip=skip,
                    take=take,
                ),
                "github": lambda: (_ for _ in ()).throw(
                    ValueError("GitHub does not support `board list`. Use `board search` instead.")
                ),
            },
        )

    def execute_board_search(
        self,
        *,
        provider: str,
        query: str,
        project: str | None,
        repo: str | None,
        area: str | None,
        work_item_type: str | None,
        state: str | None,
        assigned_to: str | None,
        skip: int,
        take: int,
    ) -> dict[str, Any]:
        single_provider = self._normalize_single_provider(provider, command="board.search")
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self.search_work_items(
                    query=query,
                    project=project,
                    area=area,
                    work_item_type=work_item_type,
                    state=state,
                    assigned_to=assigned_to,
                    skip=skip,
                    take=take,
                ),
                "github": lambda: self._github_search_work_items(
                    query=query,
                    project=project,
                    repo=repo,
                    state=state,
                    assigned_to=assigned_to,
                    skip=skip,
                    take=take,
                    include_closed=True,
                ),
            },
        )

    def execute_board_mine(
        self,
        *,
        provider: str,
        project: str | None,
        repo: str | None,
        include_closed: bool,
        skip: int,
        take: int,
    ) -> dict[str, Any]:
        single_provider = self._normalize_single_provider(provider, command="board.mine")
        return self._fanout(
            provider=single_provider,
            operations={
                "azdo": lambda: self.get_my_work_items(
                    project=project,
                    include_closed=include_closed,
                    skip=skip,
                    take=take,
                ),
                "github": lambda: self._github_get_my_work_items(
                    project=project,
                    repo=repo,
                    include_closed=include_closed,
                    skip=skip,
                    take=take,
                ),
            },
        )
