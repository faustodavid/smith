from __future__ import annotations

from pathlib import Path

from smith.benchmark.copilot_sdk import (
    GITHUB_AUTH_HEADER_ENV,
    GITHUB_MCP_SERVER_NAME,
    SMITH_MCP_SERVER_NAME,
    build_copilot_auth_env,
    build_github_copilot_env,
    build_github_copilot_payload,
    build_smith_copilot_payload,
    copilot_tool_name,
    summarize_copilot_events,
)


def test_build_smith_copilot_payload_uses_local_mcp_and_single_tool():
    payload = build_smith_copilot_payload(
        model="gpt-5.4",
        prompt="Find the repos.",
        system_message="Skill body",
        repo_root=Path("/tmp/smith"),
        env={"PYTHONPATH": "/existing/path"},
    )

    expected_tool = copilot_tool_name(SMITH_MCP_SERVER_NAME, "smith_cli")

    assert payload["availableTools"] == [expected_tool]
    assert payload["mcpServers"][SMITH_MCP_SERVER_NAME]["tools"] == ["smith_cli"]
    assert payload["mcpServers"][SMITH_MCP_SERVER_NAME]["env"]["GH_TOKEN"] == "__ENV__:GH_TOKEN"
    assert payload["mcpServers"][SMITH_MCP_SERVER_NAME]["env"]["GITHUB_TOKEN"] == "__ENV__:GITHUB_TOKEN"
    assert payload["mcpServers"][SMITH_MCP_SERVER_NAME]["env"]["PYTHONPATH"] == "/tmp/smith/src:/existing/path"


def test_build_github_copilot_payload_uses_remote_mcp_and_expected_tools():
    payload = build_github_copilot_payload(
        model="gpt-5.4",
        prompt="Find the repos.",
        system_message="Generic instructions",
        github_mcp_url="https://api.githubcopilot.com/mcp/",
    )

    assert payload["availableTools"] == [
        copilot_tool_name(GITHUB_MCP_SERVER_NAME, "search_code"),
        copilot_tool_name(GITHUB_MCP_SERVER_NAME, "get_file_contents"),
    ]
    assert payload["mcpServers"][GITHUB_MCP_SERVER_NAME]["tools"] == ["search_code", "get_file_contents"]
    assert payload["mcpServers"][GITHUB_MCP_SERVER_NAME]["headers"]["Authorization"] == (
        f"__ENV__:{GITHUB_AUTH_HEADER_ENV}"
    )


def test_build_github_copilot_env_adds_bearer_header_from_gh_token():
    env = build_github_copilot_env(env={"GH_TOKEN": "secret-token"})

    assert env[GITHUB_AUTH_HEADER_ENV] == "Bearer secret-token"
    assert env["GH_TOKEN"] == "secret-token"
    assert env["GITHUB_TOKEN"] == "secret-token"


def test_build_copilot_auth_env_promotes_copilot_github_token():
    env = build_copilot_auth_env(env={"COPILOT_GITHUB_TOKEN": "secret-token"})

    assert env["GH_TOKEN"] == "secret-token"
    assert env["GITHUB_TOKEN"] == "secret-token"


def test_build_copilot_auth_env_populates_gh_token_from_gh_auth_fallback(monkeypatch):
    monkeypatch.delenv("COPILOT_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        "smith.benchmark.copilot_sdk.resolve_github_mcp_token",
        lambda _env=None: "secret-token",
    )

    env = build_copilot_auth_env()

    assert env["GH_TOKEN"] == "secret-token"


def test_summarize_copilot_events_aggregates_usage_and_tool_calls():
    events = [
        {"type": "tool.execution_start", "data": {"toolName": "github-benchmark-search_code"}},
        {
            "type": "assistant.usage",
            "data": {
                "inputTokens": 120,
                "outputTokens": 30,
                "cacheReadTokens": 40,
                "cacheWriteTokens": 5,
                "duration": 1800,
            },
        },
        {
            "type": "assistant.usage",
            "data": {
                "inputTokens": 80,
                "outputTokens": 20,
                "cacheReadTokens": 0,
                "cacheWriteTokens": 0,
                "duration": 700,
            },
        },
    ]

    summary = summarize_copilot_events(events)

    assert summary["tool_calls"] == {"github-benchmark-search_code": 1}
    assert summary["total_tool_calls"] == 1
    assert summary["input_tokens"] == 200
    assert summary["output_tokens"] == 50
    assert summary["total_tokens"] == 250
    assert summary["cache_read_tokens"] == 40
    assert summary["cache_write_tokens"] == 5
    assert summary["api_duration_ms"] == 2500
