from __future__ import annotations

import json
import os
import shlex
import statistics
import subprocess
import sys
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

from smith.benchmark.github_mcp import DEFAULT_GITHUB_MCP_URL, build_github_mcp_server
from smith.benchmark.grading import grade_run_directory

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


def validate_smith_cli_command(command: str) -> list[str]:
    tokens = shlex.split(command)
    if not tokens:
        raise ValueError("smith_cli requires a non-empty command.")
    if tokens[0] == "smith":
        tokens = tokens[1:]
    if not tokens:
        raise ValueError("smith_cli requires Smith subcommand arguments after `smith`.")

    if tokens[:2] == ["code", "search"]:
        if "--project" in tokens:
            raise ValueError("smith_cli only supports GitHub code search for this benchmark.")
        if "--provider" in tokens:
            provider_index = tokens.index("--provider") + 1
            if provider_index >= len(tokens) or tokens[provider_index] != "github":
                raise ValueError("smith_cli only allows --provider github for code search.")
        else:
            tokens.extend(["--provider", "github"])
        return tokens

    if len(tokens) >= 3 and tokens[:3] == ["code", "grep", "github"]:
        return tokens

    if tokens[:2] == ["repos", "github"]:
        return tokens

    if tokens[:2] == ["orgs", "github"]:
        return tokens

    raise ValueError(
        "smith_cli only allows: `code search`, `code grep github`, `repos github`, and `orgs github`."
    )


def build_smith_cli_subprocess(
    command: str,
    *,
    repo_root: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
) -> tuple[list[str], dict[str, str]]:
    tokens = validate_smith_cli_command(command)
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    run_env["GITHUB_ORG"] = "grafana"
    existing_pythonpath = run_env.get("PYTHONPATH", "")
    src_path = str(repo_root / "src")
    run_env["PYTHONPATH"] = src_path if not existing_pythonpath else f"{src_path}{os.pathsep}{existing_pythonpath}"
    return [sys.executable, "-m", "smith.cli.main", *tokens], run_env


def execute_smith_cli_command(command: str) -> str:
    argv, env = build_smith_cli_subprocess(command)
    completed = subprocess.run(
        argv,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if completed.returncode != 0:
        details = stderr or stdout or f"smith exited with code {completed.returncode}"
        raise RuntimeError(details)
    return stdout or "(no output)"


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


def render_transcript(new_items: list[Any]) -> str:
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


def build_metrics(result: Any, *, final_answer: str, transcript: str, had_error: bool) -> dict[str, Any]:
    tool_counts: Counter[str] = Counter()
    for item in getattr(result, "new_items", []):
        if isinstance(item, ToolCallItem):
            tool_counts[_extract_tool_name(item.raw_item)] += 1

    return {
        "tool_calls": dict(sorted(tool_counts.items())),
        "total_tool_calls": sum(tool_counts.values()),
        "total_steps": len(getattr(result, "new_items", [])),
        "files_created": ["final_answer.md", "transcript.md"],
        "errors_encountered": 1 if had_error else 0,
        "output_chars": len(final_answer),
        "transcript_chars": len(transcript),
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
    @function_tool
    def smith_cli(command: str) -> str:
        """Run a read-only Smith command against GitHub repositories in the Grafana org."""

        return execute_smith_cli_command(command)

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
        "Do not guess. Report only real source-code implementations, ignore docs and examples, "
        "and finish with a Sources section that lists repo:path entries."
    )


async def run_agent_once(
    *,
    config_name: str,
    model: str,
    prompt: str,
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    run_config = RunConfig(tracing_disabled=True)
    server = None
    result = None
    started = datetime.now(timezone.utc)
    had_error = False

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
        transcript = render_transcript(result.new_items)
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
        metrics = build_metrics(
            result,
            final_answer=final_answer,
            transcript=transcript,
            had_error=had_error,
        )
        if result is not None:
            result.release_agents()
        if server is not None:
            await server.cleanup()

    return final_answer, transcript, metrics, timing


async def execute_eval_run(
    *,
    workspace: Path,
    eval_case: BenchmarkEval,
    config_name: str,
    run_number: int,
    model: str,
) -> None:
    run_dir = workspace / f"eval-{eval_case.id}" / config_name / f"run-{run_number}"
    outputs_dir = run_dir / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    final_answer, transcript, metrics, timing = await run_agent_once(
        config_name=config_name,
        model=model,
        prompt=eval_case.prompt,
    )

    (outputs_dir / "final_answer.md").write_text(final_answer.rstrip() + "\n")
    (outputs_dir / "transcript.md").write_text(transcript)
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
) -> tuple[dict[str, Any], str]:
    runs: list[dict[str, Any]] = []
    by_config: dict[str, list[dict[str, Any]]] = {}

    for eval_case in evals_run:
        eval_dir = workspace / f"eval-{eval_case.id}"
        for config_name in CONFIG_ORDER:
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

    run_summary: dict[str, Any] = {}
    for config_name in CONFIG_ORDER:
        entries = by_config.get(config_name, [])
        run_summary[config_name] = {
            "pass_rate": _calculate_stats([entry["result"]["pass_rate"] for entry in entries]),
            "time_seconds": _calculate_stats([float(entry["result"]["time_seconds"]) for entry in entries]),
            "tokens": _calculate_stats([float(entry["result"]["tokens"]) for entry in entries]),
        }

    primary = run_summary.get(CONFIG_ORDER[0], {})
    baseline = run_summary.get(CONFIG_ORDER[1], {})
    run_summary["delta"] = {
        "pass_rate": f"{primary.get('pass_rate', {}).get('mean', 0.0) - baseline.get('pass_rate', {}).get('mean', 0.0):+.2f}",
        "time_seconds": f"{primary.get('time_seconds', {}).get('mean', 0.0) - baseline.get('time_seconds', {}).get('mean', 0.0):+.1f}",
        "tokens": f"{primary.get('tokens', {}).get('mean', 0.0) - baseline.get('tokens', {}).get('mean', 0.0):+.0f}",
    }

    notes = [
        "Single-eval baseline benchmark; treat variance and generalization cautiously.",
    ]
    smith_tokens = run_summary["smith_skill"]["tokens"]["mean"]
    mcp_tokens = run_summary["github_mcp"]["tokens"]["mean"]
    if smith_tokens or mcp_tokens:
        direction = "lower" if smith_tokens < mcp_tokens else "higher"
        notes.append(
            f"Smith token usage is {direction} than GitHub MCP on average ({smith_tokens:.0f} vs {mcp_tokens:.0f})."
        )

    benchmark = {
        "metadata": {
            "skill_name": "smith",
            "skill_path": str(SKILL_PATH),
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
        f"Model: `{model}`",
        f"Workspace: `{workspace}`",
        "",
        "| Configuration | Mean Pass Rate | Mean Time (s) | Mean Tokens |",
        "| --- | ---: | ---: | ---: |",
    ]
    for config_name in CONFIG_ORDER:
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
                )

    benchmark, benchmark_markdown = aggregate_workspace(
        workspace=workspace_path,
        evals_run=evals_run,
        model=model,
    )
    (workspace_path / "benchmark.json").write_text(json.dumps(benchmark, indent=2, sort_keys=True) + "\n")
    (workspace_path / "benchmark.md").write_text(benchmark_markdown.rstrip() + "\n")
    return workspace_path
