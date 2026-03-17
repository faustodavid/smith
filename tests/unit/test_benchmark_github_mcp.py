from __future__ import annotations

from mcp.types import Tool

from smith.benchmark.github_mcp import (
    POST_ONLY_GITHUB_MCP_URLS,
    build_github_mcp_server,
    github_mcp_tool_filter,
    normalize_github_mcp_url,
    resolve_github_mcp_token,
)


def test_build_github_mcp_server_adds_bearer_headers_and_registers_post_only_url():
    server = build_github_mcp_server(
        url="https://api.githubcopilot.com/mcp",
        token="ghp_test_token",
    )

    assert server.params["headers"] == {"Authorization": "Bearer ghp_test_token"}
    assert normalize_github_mcp_url("https://api.githubcopilot.com/mcp") in POST_ONLY_GITHUB_MCP_URLS


def test_github_mcp_tool_filter_only_allows_search_and_get_file_contents():
    allowed_tool = Tool(name="search_code", description="search", inputSchema={})
    file_tool = Tool(name="get_file_contents", description="file", inputSchema={})
    blocked_tool = Tool(name="list_repositories", description="repos", inputSchema={})

    assert github_mcp_tool_filter(None, allowed_tool) is True
    assert github_mcp_tool_filter(None, file_tool) is True
    assert github_mcp_tool_filter(None, blocked_tool) is False


def test_resolve_github_mcp_token_accepts_gh_token():
    assert resolve_github_mcp_token({"GH_TOKEN": "ghs_test_token"}) == "ghs_test_token"


def test_resolve_github_mcp_token_accepts_copilot_github_token():
    assert resolve_github_mcp_token({"COPILOT_GITHUB_TOKEN": "ghs_test_token"}) == "ghs_test_token"
