from __future__ import annotations

import pytest

from smith.benchmark.codex_cli import (
    build_github_codex_prompt,
    build_smith_codex_prompt,
    extract_codex_last_agent_message,
    find_unexpected_codex_tool_usage,
    parse_codex_jsonl,
    resolve_codex_cli_path,
    summarize_codex_events,
)


def test_build_smith_codex_prompt_mentions_server_and_tool_constraints():
    prompt = build_smith_codex_prompt(task_prompt="Find the repos.", skill_body="Use Smith.")

    assert "smith-benchmark" in prompt
    assert "smith_cli" in prompt
    assert "Do not use local shell commands" in prompt
    assert "Find the repos." in prompt


def test_build_github_codex_prompt_mentions_server_and_allowed_tools():
    prompt = build_github_codex_prompt(task_prompt="Find the config.", instructions="Use the repo.")

    assert "github-benchmark" in prompt
    assert "search_code" in prompt
    assert "get_file_contents" in prompt
    assert "Do not use local shell commands" in prompt
    assert "Find the config." in prompt


def test_resolve_codex_cli_path_honors_provided_path_env(monkeypatch):
    monkeypatch.setattr("smith.benchmark.codex_cli.MACOS_CODEX_CLI_CANDIDATES", ())
    monkeypatch.setattr(
        "smith.benchmark.codex_cli.shutil.which",
        lambda cmd, path=None: "/mock/bin/codex"
        if cmd == "codex" and path == "/Applications/Codex.app/Contents/Resources:/usr/bin:/bin"
        else None,
    )

    resolved = resolve_codex_cli_path(
        env={"PATH": "/Applications/Codex.app/Contents/Resources:/usr/bin:/bin"}
    )

    assert resolved == "/mock/bin/codex"


def test_resolve_codex_cli_path_falls_back_to_known_app_bundle(tmp_path, monkeypatch):
    codex_path = tmp_path / "Codex.app" / "Contents" / "Resources" / "codex"
    codex_path.parent.mkdir(parents=True)
    codex_path.write_text("#!/bin/sh\n")
    codex_path.chmod(0o755)
    monkeypatch.setattr("smith.benchmark.codex_cli.MACOS_CODEX_CLI_CANDIDATES", (codex_path,))

    resolved = resolve_codex_cli_path(env={"PATH": "/usr/bin:/bin"})

    assert resolved == str(codex_path)


def test_resolve_codex_cli_path_raises_when_missing(monkeypatch):
    monkeypatch.setattr("smith.benchmark.codex_cli.MACOS_CODEX_CLI_CANDIDATES", ())

    with pytest.raises(FileNotFoundError):
        resolve_codex_cli_path(env={"PATH": "/usr/bin:/bin"})


def test_parse_codex_jsonl_skips_non_json_lines():
    stdout = "\n".join(
        [
            "not-json",
            '{"type":"thread.started","thread_id":"abc"}',
            "",
            '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":3,"output_tokens":2}}',
        ]
    )

    events = parse_codex_jsonl(stdout)

    assert events == [
        {"type": "thread.started", "thread_id": "abc"},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 3, "output_tokens": 2}},
    ]


def test_extract_codex_last_agent_message_returns_last_message():
    events = [
        {"type": "item.completed", "item": {"type": "agent_message", "text": "first"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "second"}},
    ]

    assert extract_codex_last_agent_message(events) == "second"


def test_summarize_codex_events_aggregates_usage_and_tool_counts():
    events = [
        {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "server": "github-benchmark",
                "tool": "search_code",
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "ls",
                "status": "completed",
            },
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 120,
                "cached_input_tokens": 90,
                "output_tokens": 25,
            },
        },
    ]

    summary = summarize_codex_events(events)

    assert summary["tool_calls"] == {
        "command_execution": 1,
        "github-benchmark:search_code": 1,
    }
    assert summary["total_tool_calls"] == 2
    assert summary["input_tokens"] == 120
    assert summary["cached_input_tokens"] == 90
    assert summary["output_tokens"] == 25
    assert summary["total_tokens"] == 145


def test_find_unexpected_codex_tool_usage_flags_shell_and_unknown_mcp_calls():
    events = [
        {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "server": "github-benchmark",
                "tool": "search_code",
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "server": "codex",
                "tool": "list_mcp_resources",
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "server": "github-benchmark",
                "tool": "list_repositories",
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "ls -la",
                "status": "completed",
            },
        },
    ]

    unexpected = find_unexpected_codex_tool_usage(events, config_name="github_mcp")

    assert unexpected == [
        "github-benchmark:list_repositories",
        "command_execution:ls -la",
    ]
