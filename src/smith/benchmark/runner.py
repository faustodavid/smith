from __future__ import annotations

import asyncio
import json
import os
import shutil
import statistics
import subprocess
import traceback
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents import Agent, Runner, function_tool
from agents.items import MessageOutputItem, ReasoningItem, ToolCallItem, ToolCallOutputItem
from agents.model_settings import ModelSettings, Reasoning
from agents.run import RunConfig

from smith.benchmark.codex_cli import (
    add_github_codex_mcp_server,
    add_smith_codex_mcp_server,
    build_github_codex_prompt,
    build_smith_codex_prompt,
    create_codex_home,
    extract_codex_last_agent_message,
    find_unexpected_codex_tool_usage,
    parse_codex_jsonl,
    render_codex_transcript,
    resolve_codex_cli_path,
    summarize_codex_events,
)
from smith.benchmark.copilot_sdk import (
    build_copilot_auth_env,
    build_github_copilot_env,
    build_github_copilot_payload,
    build_smith_copilot_payload,
    render_copilot_transcript,
    run_copilot_payload,
    summarize_copilot_events,
)
from smith.benchmark.github_mcp import DEFAULT_GITHUB_MCP_URL, build_github_mcp_server
from smith.benchmark.grading import grade_run_directory
from smith.benchmark.smith_cli import InProcessSmithCliRunner, execute_smith_cli_command

REPO_ROOT = Path(__file__).resolve().parents[3]
EVALS_PATH = REPO_ROOT / "evals" / "evals.json"
SKILL_PATH = REPO_ROOT / "SKILL.md"
DEFAULT_WORKSPACE_ROOT = REPO_ROOT / "benchmarks" / "workspaces"
CONFIG_ORDER = ("smith_skill", "github_mcp")


@dataclass(frozen=True)
class BenchmarkEval:
    id: int
    name: str
    prompt: str
    expected_output: str
    expectations: list[str]


def load_skill_body(skill_path: Path = SKILL_PATH) -> str:
    text = skill_path.read_text()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2].lstrip()
    return text


def load_evals(evals_path: Path = EVALS_PATH) -> list[BenchmarkEval]:
    payload = json.loads(evals_path.read_text())
    evals: list[BenchmarkEval] = []
    for entry in payload.get("evals", []):
        evals.append(
            BenchmarkEval(
                id=int(entry["id"]),
                name=str(entry.get("name") or f"eval-{entry['id']}"),
                prompt=str(entry["prompt"]),
                expected_output=str(entry.get("expected_output", "")),
                expectations=[str(item) for item in entry.get("expectations", [])],
            )
        )
    return evals


def resolve_eval_selection(evals: list[BenchmarkEval], selection: list[int] | None) -> list[BenchmarkEval]:
    if not selection:
        return evals
    requested = set(selection)
    return [item for item in evals if item.id in requested]


def resolve_workspace_path(workspace: Path | None = None) -> Path:
    if workspace is not None:
        return workspace
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    return DEFAULT_WORKSPACE_ROOT / timestamp


def parse_eval_ids(raw_value: str) -> list[int] | None:
    value = raw_value.strip().lower()
    if value == "all":
        return None
    parsed: list[int] = []
    for chunk in raw_value.split(","):
        item = chunk.strip()
        if not item:
            continue
        parsed.append(int(item))
    return parsed


def normalize_config_selection(raw_config: str) -> list[str]:
    if raw_config == "all":
        return list(CONFIG_ORDER)
    if raw_config not in CONFIG_ORDER:
        raise ValueError(f"Unsupported configuration: {raw_config}")
    return [raw_config]


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(exclude_none=True))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)


def _extract_tool_name(raw_item: Any) -> str:
    payload = _jsonable(raw_item)
    if isinstance(payload, dict):
        if isinstance(payload.get("name"), str):
            return payload["name"]
        if isinstance(payload.get("tool_name"), str):
            return payload["tool_name"]
    return "unknown_tool"


def _normalize_tool_arguments(value: Any) -> Any:
    normalized = _jsonable(value)
    if isinstance(normalized, str):
        stripped = normalized.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return normalized
    return normalized


def _build_result_preview(value: Any, *, max_chars: int = 1200) -> str:
    if value is None:
        return ""

    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, list):
            snippets: list[str] = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    snippets.append(item["text"])
            if snippets:
                text = "\n".join(snippets)
            else:
                text = json.dumps(_jsonable(value), indent=2, sort_keys=True)
        else:
            text = json.dumps(_jsonable(value), indent=2, sort_keys=True)
    elif isinstance(value, str):
        text = value
    else:
        text = json.dumps(_jsonable(value), indent=2, sort_keys=True)

    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n...[truncated]"


def _extract_openai_call_id(payload: dict[str, Any]) -> str | None:
    for key in ("call_id", "tool_call_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def build_openai_tool_trace(new_items: list[Any]) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    call_index: dict[str, dict[str, Any]] = {}

    for item in new_items:
        if isinstance(item, ToolCallItem):
            payload = _jsonable(item.raw_item)
            if not isinstance(payload, dict):
                payload = {"raw_item": payload}
            arguments = None
            for key in ("arguments", "input", "params"):
                if key in payload:
                    arguments = _normalize_tool_arguments(payload[key])
                    break
            entry = {
                "step": len(trace) + 1,
                "kind": "function_tool_call",
                "server": None,
                "tool": _extract_tool_name(item.raw_item),
                "status": "completed",
                "arguments": arguments,
            }
            trace.append(entry)
            call_id = _extract_openai_call_id(payload)
            if call_id:
                call_index[call_id] = entry
            continue

        if not isinstance(item, ToolCallOutputItem):
            continue

        payload = _jsonable(item.raw_item)
        if not isinstance(payload, dict):
            payload = {"raw_item": payload}
        output = _jsonable(item.output)
        call_id = _extract_openai_call_id(payload)
        output_entry: dict[str, Any] | None = call_index.get(call_id) if call_id else None
        if output_entry is None:
            output_entry = {
                "step": len(trace) + 1,
                "kind": "function_tool_output",
                "server": None,
                "tool": _extract_tool_name(payload),
                "status": "completed",
                "arguments": None,
            }
            trace.append(output_entry)
        preview = _build_result_preview(output)
        if preview:
            output_entry["result_preview"] = preview
        if payload.get("error") is not None:
            output_entry["error"] = _jsonable(payload["error"])

    return trace


def _split_copilot_tool_name(tool_name: str) -> tuple[str | None, str]:
    if "-" not in tool_name:
        return None, tool_name
    server, tool = tool_name.rsplit("-", 1)
    return server, tool


def build_copilot_tool_trace(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}

    for event in events:
        event_type = str(event.get("type") or "")
        data = event.get("data") or {}
        if not isinstance(data, dict) or not event_type.startswith("tool.execution"):
            continue

        tool_name = str(data.get("toolName") or "unknown_tool")
        server, tool = _split_copilot_tool_name(tool_name)
        key = str(data.get("toolCallId") or data.get("executionId") or f"{tool_name}:{len(trace)}")

        if event_type == "tool.execution_start":
            entry = {
                "step": len(trace) + 1,
                "kind": "tool_execution",
                "server": server,
                "tool": tool,
                "status": "started",
                "arguments": _normalize_tool_arguments(
                    data.get("arguments", data.get("input", data.get("params")))
                ),
            }
            trace.append(entry)
            pending[key] = entry
            continue

        execution_entry: dict[str, Any] | None = pending.get(key)
        if execution_entry is None:
            execution_entry = {
                "step": len(trace) + 1,
                "kind": "tool_execution",
                "server": server,
                "tool": tool,
                "status": event_type.removeprefix("tool.execution_"),
                "arguments": _normalize_tool_arguments(
                    data.get("arguments", data.get("input", data.get("params")))
                ),
            }
            trace.append(execution_entry)
        else:
            execution_entry["status"] = event_type.removeprefix("tool.execution_")
            if execution_entry.get("arguments") is None:
                execution_entry["arguments"] = _normalize_tool_arguments(
                    data.get("arguments", data.get("input", data.get("params")))
                )

        if data.get("error") is not None:
            execution_entry["error"] = _jsonable(data["error"])
        preview = _build_result_preview(data.get("output", data.get("result")))
        if preview:
            execution_entry["result_preview"] = preview

    return trace


def build_codex_tool_trace(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []

    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item") or {}
        if not isinstance(item, dict):
            continue

        item_type = str(item.get("type") or "")
        if item_type == "mcp_tool_call":
            entry = {
                "step": len(trace) + 1,
                "kind": "mcp_tool_call",
                "server": item.get("server"),
                "tool": item.get("tool"),
                "status": item.get("status"),
                "arguments": _normalize_tool_arguments(item.get("arguments")),
            }
            if item.get("error") is not None:
                entry["error"] = _jsonable(item["error"])
            preview = _build_result_preview(item.get("result"))
            if preview:
                entry["result_preview"] = preview
            trace.append(entry)
            continue

        if item_type != "command_execution":
            continue

        entry = {
            "step": len(trace) + 1,
            "kind": "command_execution",
            "server": None,
            "tool": "command_execution",
            "status": item.get("status"),
            "arguments": {
                "command": item.get("command"),
            },
        }
        if item.get("exit_code") is not None:
            entry["exit_code"] = item.get("exit_code")
        preview = _build_result_preview(item.get("aggregated_output"))
        if preview:
            entry["result_preview"] = preview
        trace.append(entry)

    return trace


def render_tool_trace_markdown(tool_trace: list[dict[str, Any]]) -> str:
    lines = [
        "# Tool Trace",
        "",
        "This file records visible tool calls and arguments from the benchmark run.",
        "It does not include hidden chain-of-thought.",
        "",
        "## Summary",
        f"- Total tool calls: `{len(tool_trace)}`",
        "",
        "## Calls",
    ]

    if not tool_trace:
        lines.extend(["No tool calls were recorded.", ""])
        return "\n".join(lines).rstrip() + "\n"

    for entry in tool_trace:
        server = entry.get("server")
        tool_label = str(entry.get("tool") or "unknown_tool")
        if server:
            tool_label = f"{server}:{tool_label}"
        lines.append(f"### Step {entry.get('step', '?')}: `{tool_label}` `{entry.get('status', 'unknown')}`")
        lines.append("Arguments:")
        lines.append("```json")
        lines.append(json.dumps(_jsonable(entry.get("arguments")), indent=2, sort_keys=True))
        lines.append("```")
        if entry.get("error") is not None:
            lines.append("Error:")
            lines.append("```json")
            lines.append(json.dumps(_jsonable(entry.get("error")), indent=2, sort_keys=True))
            lines.append("```")
        preview = str(entry.get("result_preview") or "").strip()
        if preview:
            lines.append("Result preview:")
            lines.append("```text")
            lines.append(preview)
            lines.append("```")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_openai_transcript(new_items: list[Any]) -> str:
    lines = ["# Transcript", ""]
    for index, item in enumerate(new_items, start=1):
        lines.append(f"## Item {index}: {item.type}")
        if isinstance(item, MessageOutputItem):
            lines.append("```json")
            lines.append(json.dumps(_jsonable(item.raw_item), indent=2, sort_keys=True))
            lines.append("```")
        elif isinstance(item, ReasoningItem):
            lines.append("```json")
            lines.append(json.dumps(_jsonable(item.raw_item), indent=2, sort_keys=True))
            lines.append("```")
        elif isinstance(item, ToolCallItem):
            lines.append(f"Tool: `{_extract_tool_name(item.raw_item)}`")
            lines.append("```json")
            lines.append(json.dumps(_jsonable(item.raw_item), indent=2, sort_keys=True))
            lines.append("```")
        elif isinstance(item, ToolCallOutputItem):
            lines.append("```json")
            lines.append(json.dumps(_jsonable(item.raw_item), indent=2, sort_keys=True))
            lines.append("```")
            lines.append("### Output")
            lines.append("```json")
            lines.append(json.dumps(_jsonable(item.output), indent=2, sort_keys=True))
            lines.append("```")
        else:
            lines.append("```json")
            lines.append(json.dumps(_jsonable(getattr(item, "raw_item", item)), indent=2, sort_keys=True))
            lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_openai_metrics(result: Any, *, final_answer: str, transcript: str, had_error: bool) -> dict[str, Any]:
    tool_counts: Counter[str] = Counter()
    for item in getattr(result, "new_items", []):
        if isinstance(item, ToolCallItem):
            tool_counts[_extract_tool_name(item.raw_item)] += 1

    return {
        "tool_calls": dict(sorted(tool_counts.items())),
        "total_tool_calls": sum(tool_counts.values()),
        "total_steps": len(getattr(result, "new_items", [])),
        "files_created": ["final_answer.md", "transcript.md", "tool_trace.json", "tool_trace.md"],
        "errors_encountered": 1 if had_error else 0,
        "output_chars": len(final_answer),
        "transcript_chars": len(transcript),
    }


def build_copilot_metrics(
    events: list[dict[str, Any]],
    *,
    final_answer: str,
    transcript: str,
    usage_summary: dict[str, Any],
    had_error: bool,
) -> dict[str, Any]:
    return {
        "tool_calls": dict(sorted((usage_summary.get("tool_calls") or {}).items())),
        "total_tool_calls": int(usage_summary.get("total_tool_calls", 0) or 0),
        "total_steps": int(usage_summary.get("total_steps", len(events)) or 0),
        "files_created": ["final_answer.md", "transcript.md", "tool_trace.json", "tool_trace.md"],
        "errors_encountered": 1 if had_error else 0,
        "output_chars": len(final_answer),
        "transcript_chars": len(transcript),
        "input_tokens": int(usage_summary.get("input_tokens", 0) or 0),
        "output_tokens": int(usage_summary.get("output_tokens", 0) or 0),
        "cache_read_tokens": int(usage_summary.get("cache_read_tokens", 0) or 0),
        "cache_write_tokens": int(usage_summary.get("cache_write_tokens", 0) or 0),
    }


def default_model_settings() -> ModelSettings:
    return ModelSettings(
        tool_choice="required",
        parallel_tool_calls=False,
        include_usage=True,
        reasoning=Reasoning(effort="low", summary="concise"),
        verbosity="low",
        store=False,
    )


def build_smith_agent(model: str) -> Agent[Any]:
    runner = InProcessSmithCliRunner()

    @function_tool
    def smith_cli(command: str) -> str:
        """Run a read-only Smith command against GitHub repositories in the OpenAI org."""

        return execute_smith_cli_command(command, runner=runner)

    return Agent(
        name="smith_skill_benchmark",
        instructions=load_skill_body(),
        tools=[smith_cli],
        model=model,
        model_settings=default_model_settings(),
    )


def github_mcp_instructions() -> str:
    return (
        "Investigate GitHub repositories with the available read-only tools. "
        "Start with search_code to find candidate repositories or files, then use "
        "get_file_contents only on the smallest number of files needed to verify exact evidence. "
        "Do not guess. Answer with exact keys, paths, values, or file evidence from the repo, "
        "and finish with a Sources section that lists repo:path entries."
    )


async def run_openai_agent_once(
    *,
    config_name: str,
    model: str,
    prompt: str,
) -> tuple[str, str, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    run_config = RunConfig(tracing_disabled=True)
    server = None
    result = None
    started = datetime.now(timezone.utc)
    had_error = False
    tool_trace: list[dict[str, Any]] = []

    try:
        if config_name == "smith_skill":
            agent = build_smith_agent(model)
        elif config_name == "github_mcp":
            server = build_github_mcp_server(
                url=os.getenv("GITHUB_MCP_SERVER_URL", DEFAULT_GITHUB_MCP_URL)
            )
            await server.connect()
            agent = Agent(
                name="github_mcp_benchmark",
                instructions=github_mcp_instructions(),
                mcp_servers=[server],
                model=model,
                model_settings=default_model_settings(),
            )
        else:
            raise ValueError(f"Unsupported benchmark configuration: {config_name}")

        result = await Runner.run(
            starting_agent=agent,
            input=prompt,
            max_turns=20,
            run_config=run_config,
        )
        final_answer = str(result.final_output)
        transcript = render_openai_transcript(result.new_items)
        tool_trace = build_openai_tool_trace(result.new_items)
    except Exception as exc:
        had_error = True
        final_answer = f"Benchmark run failed: {exc}"
        transcript = "# Transcript\n\n```text\n" + traceback.format_exc().rstrip() + "\n```\n"
    finally:
        finished = datetime.now(timezone.utc)
        duration_seconds = round((finished - started).total_seconds(), 3)
        usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
        timing = {
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            "duration_ms": int(duration_seconds * 1000),
            "total_duration_seconds": duration_seconds,
            "executor_start": started.isoformat(),
            "executor_end": finished.isoformat(),
            "executor_duration_seconds": duration_seconds,
        }
        metrics = build_openai_metrics(
            result,
            final_answer=final_answer,
            transcript=transcript,
            had_error=had_error,
        )
        if result is not None:
            result.release_agents()
        if server is not None:
            await server.cleanup()

    return final_answer, transcript, metrics, timing, tool_trace


async def run_copilot_agent_once(
    *,
    config_name: str,
    model: str,
    prompt: str,
) -> tuple[str, str, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    started = datetime.now(timezone.utc)
    had_error = False
    tool_trace: list[dict[str, Any]] = []

    try:
        if config_name == "smith_skill":
            run_env = build_copilot_auth_env()
            payload = build_smith_copilot_payload(
                model=model,
                prompt=prompt,
                system_message=load_skill_body(),
                repo_root=REPO_ROOT,
                env=run_env,
            )
        elif config_name == "github_mcp":
            run_env = build_github_copilot_env()
            payload = build_github_copilot_payload(
                model=model,
                prompt=prompt,
                system_message=github_mcp_instructions(),
                github_mcp_url=os.getenv("GITHUB_MCP_SERVER_URL", DEFAULT_GITHUB_MCP_URL),
            )
        else:
            raise ValueError(f"Unsupported benchmark configuration: {config_name}")

        run_result = run_copilot_payload(payload, env=run_env)
        events = [dict(event) for event in run_result.get("events", [])]
        usage_summary = summarize_copilot_events(events)
        final_answer = str(run_result.get("finalAnswer", ""))
        transcript = render_copilot_transcript(events)
        tool_trace = build_copilot_tool_trace(events)
    except Exception as exc:
        had_error = True
        final_answer = f"Benchmark run failed: {exc}"
        transcript = "# Transcript\n\n```text\n" + traceback.format_exc().rstrip() + "\n```\n"
        events = []
        usage_summary = summarize_copilot_events(events)
        run_result = {}

    finished = datetime.now(timezone.utc)
    duration_ms = int(run_result.get("wallClockDurationMs") or max(0.0, (finished - started).total_seconds() * 1000))
    duration_seconds = round(duration_ms / 1000, 3)
    metrics = build_copilot_metrics(
        events,
        final_answer=final_answer,
        transcript=transcript,
        usage_summary=usage_summary,
        had_error=had_error,
    )
    timing = {
        "total_tokens": int(usage_summary.get("total_tokens", 0) or 0),
        "input_tokens": int(usage_summary.get("input_tokens", 0) or 0),
        "output_tokens": int(usage_summary.get("output_tokens", 0) or 0),
        "cache_read_tokens": int(usage_summary.get("cache_read_tokens", 0) or 0),
        "cache_write_tokens": int(usage_summary.get("cache_write_tokens", 0) or 0),
        "duration_ms": duration_ms,
        "total_duration_seconds": duration_seconds,
        "api_duration_ms": int(usage_summary.get("api_duration_ms", 0) or 0),
        "executor_start": started.isoformat(),
        "executor_end": finished.isoformat(),
        "executor_duration_seconds": duration_seconds,
    }
    return final_answer, transcript, metrics, timing, tool_trace


def build_codex_metrics(
    events: list[dict[str, Any]],
    *,
    final_answer: str,
    transcript: str,
    usage_summary: dict[str, Any],
    unexpected_tools: list[str],
    had_error: bool,
) -> dict[str, Any]:
    return {
        "tool_calls": dict(sorted((usage_summary.get("tool_calls") or {}).items())),
        "total_tool_calls": int(usage_summary.get("total_tool_calls", 0) or 0),
        "total_steps": int(usage_summary.get("total_steps", len(events)) or 0),
        "files_created": ["final_answer.md", "transcript.md", "tool_trace.json", "tool_trace.md"],
        "errors_encountered": int(usage_summary.get("errors", 0) or 0) + (1 if had_error else 0),
        "output_chars": len(final_answer),
        "transcript_chars": len(transcript),
        "input_tokens": int(usage_summary.get("input_tokens", 0) or 0),
        "output_tokens": int(usage_summary.get("output_tokens", 0) or 0),
        "cached_input_tokens": int(usage_summary.get("cached_input_tokens", 0) or 0),
        "unexpected_tools": unexpected_tools,
    }


async def run_codex_agent_once(
    *,
    config_name: str,
    model: str,
    prompt: str,
) -> tuple[str, str, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    started = datetime.now(timezone.utc)
    had_error = False
    codex_home = None
    events: list[dict[str, Any]] = []
    usage_summary = summarize_codex_events(events)
    unexpected_tools: list[str] = []
    final_answer = ""
    transcript = "# Transcript\n"
    tool_trace: list[dict[str, Any]] = []

    try:
        codex_home = create_codex_home()
        if config_name == "smith_skill":
            run_env = add_smith_codex_mcp_server(codex_home, repo_root=REPO_ROOT)
            codex_prompt = build_smith_codex_prompt(task_prompt=prompt, skill_body=load_skill_body())
        elif config_name == "github_mcp":
            run_env = add_github_codex_mcp_server(
                codex_home,
                github_mcp_url=os.getenv("GITHUB_MCP_SERVER_URL", DEFAULT_GITHUB_MCP_URL),
            )
            codex_prompt = build_github_codex_prompt(
                task_prompt=prompt,
                instructions=github_mcp_instructions(),
            )
        else:
            raise ValueError(f"Unsupported benchmark configuration: {config_name}")

        output_path = codex_home / "last_message.md"
        command = [
            resolve_codex_cli_path(run_env),
            "exec",
            "--json",
            "--model",
            model,
            "--sandbox",
            "read-only",
            "-C",
            str(REPO_ROOT),
            "-o",
            str(output_path),
            codex_prompt,
        ]
        completed = await asyncio.to_thread(
            subprocess.run,
            command,
            cwd=str(REPO_ROOT),
            env=run_env,
            capture_output=True,
            text=True,
            check=False,
        )
        events = parse_codex_jsonl(completed.stdout)
        final_answer = output_path.read_text().strip() if output_path.exists() else ""
        if not final_answer:
            final_answer = extract_codex_last_agent_message(events)
        if completed.returncode != 0:
            had_error = True
        if not final_answer and completed.returncode != 0:
            final_answer = completed.stderr.strip() or "Benchmark run failed."
        usage_summary = summarize_codex_events(events)
        unexpected_tools = find_unexpected_codex_tool_usage(events, config_name=config_name)
        if unexpected_tools:
            usage_summary["errors"] = int(usage_summary.get("errors", 0) or 0) + 1
        transcript = render_codex_transcript(events, stderr=completed.stderr)
        tool_trace = build_codex_tool_trace(events)
    except Exception as exc:
        had_error = True
        events = []
        usage_summary = summarize_codex_events(events)
        unexpected_tools = []
        final_answer = f"Benchmark run failed: {exc}"
        transcript = "# Transcript\n\n```text\n" + traceback.format_exc().rstrip() + "\n```\n"
    finally:
        finished = datetime.now(timezone.utc)
        duration_seconds = round((finished - started).total_seconds(), 3)
        metrics = build_codex_metrics(
            events,
            final_answer=final_answer,
            transcript=transcript,
            usage_summary=usage_summary,
            unexpected_tools=unexpected_tools,
            had_error=had_error,
        )
        timing = {
            "total_tokens": int(usage_summary.get("total_tokens", 0) or 0),
            "input_tokens": int(usage_summary.get("input_tokens", 0) or 0),
            "output_tokens": int(usage_summary.get("output_tokens", 0) or 0),
            "cached_input_tokens": int(usage_summary.get("cached_input_tokens", 0) or 0),
            "duration_ms": int(duration_seconds * 1000),
            "total_duration_seconds": duration_seconds,
            "executor_start": started.isoformat(),
            "executor_end": finished.isoformat(),
            "executor_duration_seconds": duration_seconds,
            "unexpected_tools": unexpected_tools,
        }
        if codex_home is not None:
            shutil.rmtree(codex_home, ignore_errors=True)

    return final_answer, transcript, metrics, timing, tool_trace


async def run_agent_once(
    *,
    config_name: str,
    model: str,
    prompt: str,
    executor: str,
) -> tuple[str, str, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    if executor == "openai":
        return await run_openai_agent_once(
            config_name=config_name,
            model=model,
            prompt=prompt,
        )
    if executor == "copilot":
        return await run_copilot_agent_once(
            config_name=config_name,
            model=model,
            prompt=prompt,
        )
    if executor == "codex":
        return await run_codex_agent_once(
            config_name=config_name,
            model=model,
            prompt=prompt,
        )
    raise ValueError(f"Unsupported benchmark executor: {executor}")


async def execute_eval_run(
    *,
    workspace: Path,
    eval_case: BenchmarkEval,
    config_name: str,
    run_number: int,
    model: str,
    executor: str,
) -> None:
    run_dir = workspace / f"eval-{eval_case.id}" / config_name / f"run-{run_number}"
    outputs_dir = run_dir / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    final_answer, transcript, metrics, timing, tool_trace = await run_agent_once(
        config_name=config_name,
        model=model,
        prompt=eval_case.prompt,
        executor=executor,
    )

    metrics["tool_trace_entries"] = len(tool_trace)
    (outputs_dir / "final_answer.md").write_text(final_answer.rstrip() + "\n")
    (outputs_dir / "transcript.md").write_text(transcript)
    (outputs_dir / "tool_trace.json").write_text(json.dumps(tool_trace, indent=2, sort_keys=True) + "\n")
    (outputs_dir / "tool_trace.md").write_text(render_tool_trace_markdown(tool_trace))
    (outputs_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")
    (run_dir / "timing.json").write_text(json.dumps(timing, indent=2, sort_keys=True) + "\n")
    grade_run_directory(run_dir, eval_case.expectations)


def _calculate_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "stddev": 0.0, "min": 0.0, "max": 0.0}
    mean = sum(values) / len(values)
    stddev = statistics.stdev(values) if len(values) > 1 else 0.0
    return {
        "mean": round(mean, 4),
        "stddev": round(stddev, 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
    }


def aggregate_workspace(
    *,
    workspace: Path,
    evals_run: list[BenchmarkEval],
    model: str,
    executor: str,
    selected_configs: list[str] | None = None,
) -> tuple[dict[str, Any], str]:
    runs: list[dict[str, Any]] = []
    by_config: dict[str, list[dict[str, Any]]] = {}
    requested_configs = list(selected_configs or CONFIG_ORDER)

    for eval_case in evals_run:
        eval_dir = workspace / f"eval-{eval_case.id}"
        for config_name in requested_configs:
            config_dir = eval_dir / config_name
            if not config_dir.exists():
                continue
            by_config.setdefault(config_name, [])
            for run_dir in sorted(config_dir.glob("run-*")):
                grading = json.loads((run_dir / "grading.json").read_text())
                timing = json.loads((run_dir / "timing.json").read_text())
                metrics = json.loads((run_dir / "outputs" / "metrics.json").read_text())
                run_number = int(run_dir.name.split("-")[1])
                entry = {
                    "eval_id": eval_case.id,
                    "eval_name": eval_case.name,
                    "configuration": config_name,
                    "run_number": run_number,
                    "result": {
                        "pass_rate": grading["summary"]["pass_rate"],
                        "passed": grading["summary"]["passed"],
                        "failed": grading["summary"]["failed"],
                        "total": grading["summary"]["total"],
                        "time_seconds": timing.get("total_duration_seconds", 0.0),
                        "tokens": timing.get("total_tokens", 0),
                        "tool_calls": metrics.get("total_tool_calls", 0),
                        "errors": metrics.get("errors_encountered", 0),
                    },
                    "expectations": grading.get("expectations", []),
                    "notes": grading.get("user_notes_summary", {}).get("uncertainties", []),
                }
                runs.append(entry)
                by_config[config_name].append(entry)

    configs_to_summarize = [config_name for config_name in requested_configs if by_config.get(config_name)]
    if not configs_to_summarize:
        raise ValueError(f"No benchmark runs found in {workspace}.")

    run_summary: dict[str, Any] = {}
    for config_name in configs_to_summarize:
        entries = by_config.get(config_name, [])
        run_summary[config_name] = {
            "pass_rate": _calculate_stats([entry["result"]["pass_rate"] for entry in entries]),
            "time_seconds": _calculate_stats([float(entry["result"]["time_seconds"]) for entry in entries]),
            "tokens": _calculate_stats([float(entry["result"]["tokens"]) for entry in entries]),
        }

    notes = [
        "Single-eval baseline benchmark; treat variance and generalization cautiously.",
    ]
    comparison_available = all(config_name in configs_to_summarize for config_name in CONFIG_ORDER)
    if comparison_available:
        primary = run_summary[CONFIG_ORDER[0]]
        baseline = run_summary[CONFIG_ORDER[1]]
        run_summary["delta"] = {
            "pass_rate": f"{primary['pass_rate']['mean'] - baseline['pass_rate']['mean']:+.2f}",
            "time_seconds": f"{primary['time_seconds']['mean'] - baseline['time_seconds']['mean']:+.1f}",
            "tokens": f"{primary['tokens']['mean'] - baseline['tokens']['mean']:+.0f}",
        }

        smith_tokens = run_summary["smith_skill"]["tokens"]["mean"]
        mcp_tokens = run_summary["github_mcp"]["tokens"]["mean"]
        if smith_tokens or mcp_tokens:
            direction = "lower" if smith_tokens < mcp_tokens else "higher"
            notes.append(
                f"Smith token usage is {direction} than GitHub MCP on average ({smith_tokens:.0f} vs {mcp_tokens:.0f})."
            )
    elif len(configs_to_summarize) == 1:
        notes.append(f"Only `{configs_to_summarize[0]}` runs are included in this workspace.")

    benchmark = {
        "metadata": {
            "skill_name": "smith",
            "skill_path": str(SKILL_PATH),
            "configurations_run": configs_to_summarize,
            "executor_backend": executor,
            "executor_model": model,
            "analyzer_model": "deterministic",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "evals_run": [eval_case.id for eval_case in evals_run],
            "runs_per_configuration": max(
                (
                    max((len(entries) for entries in by_config.values()), default=0)
                    // max(1, len(evals_run))
                ),
                0,
            ),
        },
        "runs": runs,
        "run_summary": run_summary,
        "notes": notes,
    }

    lines = [
        "# Benchmark Summary",
        "",
        f"Executor: `{executor}`",
        f"Model: `{model}`",
        f"Workspace: `{workspace}`",
        "",
        "| Configuration | Mean Pass Rate | Mean Time (s) | Mean Tokens |",
        "| --- | ---: | ---: | ---: |",
    ]
    for config_name in configs_to_summarize:
        summary = run_summary[config_name]
        lines.append(
            "| "
            f"{config_name} | "
            f"{summary['pass_rate']['mean']:.2f} | "
            f"{summary['time_seconds']['mean']:.2f} | "
            f"{summary['tokens']['mean']:.0f} |"
        )
    lines.extend(["", "## Notes", ""])
    lines.extend(f"- {note}" for note in notes)
    lines.append("")
    return benchmark, "\n".join(lines)


async def run_benchmark(
    *,
    model: str,
    runs: int = 3,
    eval_ids: list[int] | None = None,
    workspace: Path | None = None,
    config: str = "all",
    executor: str = "openai",
) -> Path:
    selected_configs = normalize_config_selection(config)
    all_evals = load_evals()
    evals_run = resolve_eval_selection(all_evals, eval_ids)
    if not evals_run:
        raise ValueError("No evals selected.")

    workspace_path = resolve_workspace_path(workspace)
    workspace_path.mkdir(parents=True, exist_ok=True)

    for eval_case in evals_run:
        eval_dir = workspace_path / f"eval-{eval_case.id}"
        eval_dir.mkdir(parents=True, exist_ok=True)
        eval_metadata = {
            "eval_id": eval_case.id,
            "eval_name": eval_case.name,
            "prompt": eval_case.prompt,
            "assertions": eval_case.expectations,
        }
        (eval_dir / "eval_metadata.json").write_text(json.dumps(eval_metadata, indent=2, sort_keys=True) + "\n")

        for config_name in selected_configs:
            for run_number in range(1, runs + 1):
                await execute_eval_run(
                    workspace=workspace_path,
                    eval_case=eval_case,
                    config_name=config_name,
                    run_number=run_number,
                    model=model,
                    executor=executor,
                )

    benchmark, benchmark_markdown = aggregate_workspace(
        workspace=workspace_path,
        evals_run=evals_run,
        model=model,
        executor=executor,
        selected_configs=selected_configs,
    )
    (workspace_path / "benchmark.json").write_text(json.dumps(benchmark, indent=2, sort_keys=True) + "\n")
    (workspace_path / "benchmark.md").write_text(benchmark_markdown.rstrip() + "\n")
    return workspace_path
