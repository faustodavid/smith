from __future__ import annotations

import functools
import os
import subprocess
from collections.abc import Mapping
from typing import Any

from agents.mcp import MCPServerStreamableHttp
from mcp.client.streamable_http import StreamableHTTPTransport

DEFAULT_GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/"
ALLOWED_GITHUB_MCP_TOOLS = frozenset({"search_code", "get_file_contents"})
GITHUB_TOKEN_ENV_VARS = ("GITHUB_TOKEN", "GH_TOKEN", "COPILOT_GITHUB_TOKEN")
POST_ONLY_GITHUB_MCP_URLS: set[str] = set()


def normalize_github_mcp_url(url: str | None) -> str:
    normalized = (url or DEFAULT_GITHUB_MCP_URL).strip()
    if not normalized:
        normalized = DEFAULT_GITHUB_MCP_URL
    return normalized.rstrip("/") + "/"


@functools.lru_cache(maxsize=1)
def patch_post_only_streamable_http() -> None:
    original = StreamableHTTPTransport.handle_get_stream

    @functools.wraps(original)
    async def _patched(self, client, read_stream_writer):
        if normalize_github_mcp_url(getattr(self, "url", "")) in POST_ONLY_GITHUB_MCP_URLS:
            return None
        return await original(self, client, read_stream_writer)

    StreamableHTTPTransport.handle_get_stream = _patched


def register_post_only_github_mcp_url(url: str | None) -> str:
    normalized = normalize_github_mcp_url(url)
    patch_post_only_streamable_http()
    POST_ONLY_GITHUB_MCP_URLS.add(normalized)
    return normalized


def build_github_mcp_headers(token: str) -> dict[str, str]:
    cleaned = token.strip()
    if not cleaned:
        raise RuntimeError("GitHub MCP requires a non-empty bearer token.")
    return {"Authorization": f"Bearer {cleaned}"}


def github_mcp_tool_filter(_context_wrapper: Any, tool: Any) -> bool:
    return str(getattr(tool, "name", "")).strip() in ALLOWED_GITHUB_MCP_TOOLS


def resolve_github_mcp_token(env: Mapping[str, str] | None = None) -> str:
    source_env = env or os.environ
    for env_var in GITHUB_TOKEN_ENV_VARS:
        explicit = str(source_env.get(env_var, "")).strip()
        if explicit:
            return explicit

    gh_env = os.environ.copy()
    gh_env.update(dict(source_env))

    result = subprocess.run(
        ["gh", "auth", "token"],
        capture_output=True,
        env=gh_env,
        text=True,
        check=False,
    )
    fallback = result.stdout.strip()
    if result.returncode == 0 and fallback:
        return fallback

    raise RuntimeError(
        "GitHub MCP authentication is unavailable. Set GITHUB_TOKEN, GH_TOKEN, or "
        "COPILOT_GITHUB_TOKEN, or run `gh auth login`."
    )


def build_github_mcp_server(
    *,
    url: str | None = None,
    token: str | None = None,
    client_session_timeout_seconds: float = 60,
) -> MCPServerStreamableHttp:
    normalized_url = register_post_only_github_mcp_url(url)
    bearer = token or resolve_github_mcp_token()
    headers = build_github_mcp_headers(bearer)
    return MCPServerStreamableHttp(
        params={"url": normalized_url, "headers": headers},
        cache_tools_list=True,
        name="GitHub MCP Benchmark",
        client_session_timeout_seconds=client_session_timeout_seconds,
        tool_filter=github_mcp_tool_filter,
        max_retry_attempts=3,
        retry_backoff_seconds_base=2.0,
    )
