from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from smith.benchmark.constants import BENCHMARK_GITHUB_ORG
from smith.benchmark.github_mcp import DEFAULT_GITHUB_MCP_URL, resolve_github_mcp_token
from smith.benchmark.smith_cli import REPO_ROOT, build_smith_pythonpath

CODEX_INTERNAL_SERVER = "codex"
CODEX_ALLOWED_DISCOVERY_TOOLS = frozenset({"list_mcp_resources", "list_mcp_resource_templates"})
CODEX_AUTH_FILENAME = "auth.json"
CODEX_CONFIG_FILENAME = "config.toml"
DEFAULT_CODEX_HOME = Path.home() / ".codex"
MACOS_CODEX_CLI_CANDIDATES = (
    Path("/Applications/Codex.app/Contents/Resources/codex"),
    Path.home() / "Applications" / "Codex.app" / "Contents" / "Resources" / "codex",
)
SMITH_CODEX_SERVER_NAME = "smith-benchmark"
SMITH_CODEX_TOOL_NAME = "smith_cli"
GITHUB_CODEX_SERVER_NAME = "github-benchmark"
GITHUB_CODEX_TOOL_NAMES = ("search_code", "get_file_contents")


def resolve_codex_cli_path(env: dict[str, str] | None = None) -> str:
    run_env = env or os.environ
    configured = run_env.get("CODEX_CLI_PATH")
    if configured:
        return configured
    resolved = shutil.which("codex", path=run_env.get("PATH"))
    if resolved:
        return resolved
    for candidate in MACOS_CODEX_CLI_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError("Could not find the `codex` CLI. Set CODEX_CLI_PATH or add `codex` to PATH.")


def resolve_codex_auth_home(env: dict[str, str] | None = None) -> Path:
    run_env = env or os.environ
    candidate = run_env.get("CODEX_AUTH_HOME") or run_env.get("CODEX_HOME")
    if candidate:
        return Path(candidate).expanduser()
    return DEFAULT_CODEX_HOME


def copy_codex_auth_into_home(
    codex_home: Path,
    *,
    env: dict[str, str] | None = None,
) -> None:
    source_home = resolve_codex_auth_home(env)
    source_auth = source_home / CODEX_AUTH_FILENAME
    if not source_auth.exists():
        raise FileNotFoundError(
            f"Could not find Codex auth at {source_auth}. Run `codex login` before using --executor codex."
        )
    codex_home.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_auth, codex_home / CODEX_AUTH_FILENAME)


def create_codex_home(
    *,
    env: dict[str, str] | None = None,
) -> Path:
    codex_home = Path(tempfile.mkdtemp(prefix="smith-codex-home."))
    copy_codex_auth_into_home(codex_home, env=env)
    return codex_home


def add_smith_codex_mcp_server(
    codex_home: Path,
    *,
    env: dict[str, str] | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, str]:
    run_env = dict(env or os.environ)
    github_token = resolve_github_mcp_token(run_env)
    codex_env = dict(run_env)
    codex_env["CODEX_HOME"] = str(codex_home)
    subprocess.run(
        [
            resolve_codex_cli_path(codex_env),
            "mcp",
            "add",
            SMITH_CODEX_SERVER_NAME,
            "--env",
            f"GITHUB_ORG={BENCHMARK_GITHUB_ORG}",
            "--env",
            f"GITHUB_TOKEN={github_token}",
            "--env",
            (
                "PYTHONPATH="
                + build_smith_pythonpath(
                    repo_root=repo_root,
                    existing_pythonpath=run_env.get("PYTHONPATH"),
                )
            ),
            "--",
            sys.executable,
            "-m",
            "smith.benchmark.smith_mcp_server",
        ],
        cwd=str(repo_root),
        env=codex_env,
        capture_output=True,
        text=True,
        check=True,
    )
    return codex_env


def add_github_codex_mcp_server(
    codex_home: Path,
    *,
    env: dict[str, str] | None = None,
    github_mcp_url: str = DEFAULT_GITHUB_MCP_URL,
) -> dict[str, str]:
    run_env = dict(env or os.environ)
    github_token = resolve_github_mcp_token(run_env)
    codex_env = dict(run_env)
    codex_env["CODEX_HOME"] = str(codex_home)
    codex_env["GITHUB_TOKEN"] = github_token
    subprocess.run(
        [
            resolve_codex_cli_path(codex_env),
            "mcp",
            "add",
            GITHUB_CODEX_SERVER_NAME,
            "--url",
            github_mcp_url,
            "--bearer-token-env-var",
            "GITHUB_TOKEN",
        ],
        cwd=str(REPO_ROOT),
        env=codex_env,
        capture_output=True,
        text=True,
        check=True,
    )
    return codex_env


def build_smith_codex_prompt(*, task_prompt: str, skill_body: str) -> str:
    return (
        "Benchmark constraints:\n"
        "- Use only the MCP server `smith-benchmark` and its `smith_cli` tool for external investigation.\n"
        "- Do not use local shell commands, local file reads, or other local tools.\n"
        "- If the MCP server is unavailable, say so explicitly instead of switching tools.\n\n"
        "Smith instructions:\n"
        f"{skill_body.strip()}\n\n"
        "Task:\n"
        f"{task_prompt.strip()}"
    )


def build_github_codex_prompt(*, task_prompt: str, instructions: str) -> str:
    return (
        "Benchmark constraints:\n"
        "- Use only the MCP server `github-benchmark`.\n"
        "- Within that server, use only `search_code` and `get_file_contents`.\n"
        "- Do not use local shell commands, local file reads, or other local tools.\n"
        "- If the MCP server is unavailable, say so explicitly instead of switching tools.\n\n"
        "Investigation instructions:\n"
        f"{instructions.strip()}\n\n"
        "Task:\n"
        f"{task_prompt.strip()}"
    )


def parse_codex_jsonl(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def summarize_codex_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    tool_counts: Counter[str] = Counter()
    error_count = 0
    input_tokens = 0
    cached_input_tokens = 0
    output_tokens = 0

    for event in events:
        event_type = str(event.get("type") or "")
        if event_type == "item.completed":
            item = event.get("item") or {}
            item_type = str(item.get("type") or "")
            if item_type == "mcp_tool_call":
                tool_counts[f"{item.get('server')}:{item.get('tool')}"] += 1
                if item.get("status") == "failed":
                    error_count += 1
            elif item_type == "command_execution":
                tool_counts["command_execution"] += 1
                if item.get("status") == "failed":
                    error_count += 1
        elif event_type == "turn.completed":
            usage = event.get("usage") or {}
            input_tokens += int(usage.get("input_tokens", 0) or 0)
            cached_input_tokens += int(usage.get("cached_input_tokens", 0) or 0)
            output_tokens += int(usage.get("output_tokens", 0) or 0)
        elif event_type in {"error", "turn.failed"}:
            error_count += 1

    return {
        "tool_calls": dict(sorted(tool_counts.items())),
        "total_tool_calls": sum(tool_counts.values()),
        "total_steps": len(events),
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "errors": error_count,
    }


def extract_codex_last_agent_message(events: list[dict[str, Any]]) -> str:
    for event in reversed(events):
        if event.get("type") != "item.completed":
            continue
        item = event.get("item") or {}
        if item.get("type") == "agent_message":
            return str(item.get("text") or "").strip()
    return ""


def find_unexpected_codex_tool_usage(
    events: list[dict[str, Any]],
    *,
    config_name: str,
) -> list[str]:
    allowed_mcp_tools = {
        "smith_skill": {(SMITH_CODEX_SERVER_NAME, SMITH_CODEX_TOOL_NAME)},
        "github_mcp": {(GITHUB_CODEX_SERVER_NAME, tool_name) for tool_name in GITHUB_CODEX_TOOL_NAMES},
    }
    allowed = allowed_mcp_tools.get(config_name, set())
    unexpected: list[str] = []

    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item") or {}
        item_type = item.get("type")
        if item_type == "command_execution":
            unexpected.append(f"command_execution:{item.get('command', '')}")
            continue
        if item_type != "mcp_tool_call":
            continue
        server = str(item.get("server") or "")
        tool = str(item.get("tool") or "")
        if server == CODEX_INTERNAL_SERVER and tool in CODEX_ALLOWED_DISCOVERY_TOOLS:
            continue
        if (server, tool) not in allowed:
            unexpected.append(f"{server}:{tool}")

    return unexpected


def render_codex_transcript(
    events: list[dict[str, Any]],
    *,
    stderr: str = "",
) -> str:
    lines = ["# Transcript", ""]
    if stderr.strip():
        lines.extend(["## Stderr", "```text", stderr.strip(), "```", ""])

    for index, event in enumerate(events, start=1):
        lines.append(f"## Event {index}: {event.get('type', 'unknown')}")
        lines.append("```json")
        lines.append(json.dumps(event, indent=2, sort_keys=True))
        lines.append("```")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
