from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from smith.benchmark.constants import BENCHMARK_GITHUB_ORG
from smith.benchmark.github_mcp import DEFAULT_GITHUB_MCP_URL, resolve_github_mcp_token
from smith.benchmark.smith_cli import REPO_ROOT, build_smith_pythonpath

COPILOT_SESSION_SCRIPT = REPO_ROOT / "scripts" / "copilot_benchmark_session.mjs"
COPILOT_DEFAULT_TIMEOUT_MS = 600_000
COPILOT_DEFAULT_CLI_ARGS = ["--no-custom-instructions", "--disable-builtin-mcps"]
COPILOT_SDK_GLOB = ".copilot/pkg/*/*/copilot-sdk/index.js"

SMITH_MCP_SERVER_NAME = "smith-benchmark"
SMITH_MCP_TOOL_NAME = "smith_cli"
GITHUB_MCP_SERVER_NAME = "github-benchmark"
GITHUB_MCP_TOOL_NAMES = ("search_code", "get_file_contents")

GITHUB_AUTH_HEADER_ENV = "SMITH_BENCHMARK_GITHUB_AUTH_HEADER"
ENV_PLACEHOLDER_PREFIX = "__ENV__:"


def resolve_copilot_cli_path(env: dict[str, str] | None = None) -> str:
    run_env = env or os.environ
    configured = run_env.get("COPILOT_CLI_PATH")
    if configured:
        return configured
    resolved = shutil.which("copilot")
    if resolved:
        return resolved
    raise FileNotFoundError("Could not find the `copilot` CLI. Set COPILOT_CLI_PATH or add `copilot` to PATH.")


def resolve_node_path() -> str:
    resolved = shutil.which("node")
    if resolved:
        return resolved
    raise FileNotFoundError("Could not find `node` on PATH.")


def resolve_copilot_sdk_path(env: dict[str, str] | None = None) -> str:
    run_env = env or os.environ
    configured = run_env.get("COPILOT_SDK_PATH")
    if configured:
        return configured

    candidates = list(Path.home().glob(COPILOT_SDK_GLOB))
    if not candidates:
        raise FileNotFoundError(
            "Could not locate the Copilot SDK. Set COPILOT_SDK_PATH to a copilot-sdk/index.js file."
        )
    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    return str(latest)


def copilot_tool_name(server_name: str, tool_name: str) -> str:
    return f"{server_name}-{tool_name}"


def _env_placeholder(env_var: str) -> str:
    return f"{ENV_PLACEHOLDER_PREFIX}{env_var}"


def build_smith_copilot_payload(
    *,
    model: str,
    prompt: str,
    system_message: str,
    repo_root: Path = REPO_ROOT,
    timeout_ms: int = COPILOT_DEFAULT_TIMEOUT_MS,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    run_env = env or os.environ
    mcp_env: dict[str, str] = {
        "GITHUB_ORG": BENCHMARK_GITHUB_ORG,
        "HOME": _env_placeholder("HOME"),
        "PATH": _env_placeholder("PATH"),
        "PYTHONPATH": build_smith_pythonpath(
            repo_root=repo_root,
            existing_pythonpath=run_env.get("PYTHONPATH"),
        ),
        "GH_TOKEN": _env_placeholder("GH_TOKEN"),
        "GITHUB_TOKEN": _env_placeholder("GITHUB_TOKEN"),
    }
    if run_env.get("GH_CONFIG_DIR"):
        mcp_env["GH_CONFIG_DIR"] = _env_placeholder("GH_CONFIG_DIR")

    return {
        "cliArgs": list(COPILOT_DEFAULT_CLI_ARGS),
        "availableTools": [copilot_tool_name(SMITH_MCP_SERVER_NAME, SMITH_MCP_TOOL_NAME)],
        "mcpServers": {
            SMITH_MCP_SERVER_NAME: {
                "type": "stdio",
                "command": sys.executable,
                "args": ["-m", "smith.benchmark.smith_mcp_server"],
                "cwd": str(repo_root),
                "env": mcp_env,
                "tools": [SMITH_MCP_TOOL_NAME],
            }
        },
        "model": model,
        "prompt": prompt,
        "reasoningEffort": "low",
        "systemMessage": system_message,
        "timeoutMs": timeout_ms,
        "workingDirectory": str(repo_root),
    }


def build_github_copilot_payload(
    *,
    model: str,
    prompt: str,
    system_message: str,
    github_mcp_url: str = DEFAULT_GITHUB_MCP_URL,
    timeout_ms: int = COPILOT_DEFAULT_TIMEOUT_MS,
) -> dict[str, Any]:
    return {
        "cliArgs": list(COPILOT_DEFAULT_CLI_ARGS),
        "availableTools": [
            copilot_tool_name(GITHUB_MCP_SERVER_NAME, tool_name) for tool_name in GITHUB_MCP_TOOL_NAMES
        ],
        "mcpServers": {
            GITHUB_MCP_SERVER_NAME: {
                "type": "http",
                "url": github_mcp_url,
                "headers": {"Authorization": _env_placeholder(GITHUB_AUTH_HEADER_ENV)},
                "tools": list(GITHUB_MCP_TOOL_NAMES),
            }
        },
        "model": model,
        "prompt": prompt,
        "reasoningEffort": "low",
        "systemMessage": system_message,
        "timeoutMs": timeout_ms,
        "workingDirectory": str(REPO_ROOT),
    }


def run_copilot_payload(
    payload: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    node_path = resolve_node_path()
    resolved_payload = dict(payload)
    resolved_payload.setdefault("cliPath", resolve_copilot_cli_path(run_env))
    resolved_payload.setdefault("sdkPath", resolve_copilot_sdk_path(run_env))
    completed = subprocess.run(
        [node_path, str(COPILOT_SESSION_SCRIPT)],
        input=json.dumps(resolved_payload),
        text=True,
        capture_output=True,
        cwd=str(REPO_ROOT),
        env=run_env,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "Copilot benchmark runner failed."
        raise RuntimeError(message)
    return json.loads(completed.stdout)


def build_github_copilot_env(
    *,
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    run_env = build_copilot_auth_env(env)
    run_env[GITHUB_AUTH_HEADER_ENV] = f"Bearer {run_env['GITHUB_TOKEN']}"
    return run_env


def build_copilot_auth_env(
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    run_env = dict(env or os.environ)
    token = resolve_github_mcp_token(run_env)
    # Normalize the benchmark env so Copilot, Smith, and GitHub MCP all use the same token source.
    run_env["GH_TOKEN"] = token
    run_env["GITHUB_TOKEN"] = token
    return run_env


def summarize_copilot_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    tool_counts: Counter[str] = Counter()
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_write_tokens = 0
    api_duration_ms = 0

    for event in events:
        event_type = event.get("type")
        data = event.get("data", {})
        if event_type == "tool.execution_start":
            tool_name = str(data.get("toolName") or "unknown_tool")
            tool_counts[tool_name] += 1
        if event_type == "assistant.usage":
            input_tokens += int(data.get("inputTokens") or 0)
            output_tokens += int(data.get("outputTokens") or 0)
            cache_read_tokens += int(data.get("cacheReadTokens") or 0)
            cache_write_tokens += int(data.get("cacheWriteTokens") or 0)
            api_duration_ms += int(data.get("duration") or 0)

    return {
        "tool_calls": dict(sorted(tool_counts.items())),
        "total_tool_calls": sum(tool_counts.values()),
        "total_steps": len(events),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "api_duration_ms": api_duration_ms,
    }


def render_copilot_transcript(events: list[dict[str, Any]]) -> str:
    lines = ["# Transcript", ""]
    for index, event in enumerate(events, start=1):
        lines.append(f"## Event {index}: {event.get('type', 'unknown')}")
        lines.append("```json")
        lines.append(json.dumps(event, indent=2, sort_keys=True))
        lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
