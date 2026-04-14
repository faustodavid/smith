from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_SCENARIOS_PATH = REPO_ROOT / "benchmarks" / "runtime" / "scenarios.json"


@dataclass(frozen=True)
class RuntimeScenario:
    key: str
    description: str
    command: list[str]
    target_improvement_pct: float


def load_runtime_scenarios(scenarios_path: Path = RUNTIME_SCENARIOS_PATH) -> list[RuntimeScenario]:
    payload = json.loads(scenarios_path.read_text())
    scenarios: list[RuntimeScenario] = []
    for entry in payload.get("scenarios", []):
        scenarios.append(
            RuntimeScenario(
                key=str(entry["key"]),
                description=str(entry["description"]),
                command=[str(item) for item in entry["command"]],
                target_improvement_pct=float(entry.get("target_improvement_pct", 0.0)),
            )
        )
    return scenarios


def _run_command(command: list[str], *, cwd: Path) -> float:
    start = time.perf_counter()
    result = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    duration = time.perf_counter() - start
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        details = stderr or stdout or "command failed"
        raise RuntimeError(f"{' '.join(command)} -> {details}")
    return duration


def _run_scenario(scenario: RuntimeScenario, *, runs: int, cwd: Path) -> dict[str, Any]:
    samples: list[float] = []
    for _ in range(runs):
        samples.append(_run_command(scenario.command, cwd=cwd))
    median = statistics.median(samples)
    return {
        "description": scenario.description,
        "command": scenario.command,
        "samples_seconds": samples,
        "median_seconds": median,
        "target_improvement_pct": scenario.target_improvement_pct,
    }


def run_runtime_scenarios(
    *,
    scenarios: list[RuntimeScenario],
    runs: int = 3,
    cwd: Path | None = None,
) -> dict[str, Any]:
    resolved_cwd = (cwd or REPO_ROOT).resolve()
    results: dict[str, Any] = {
        "runs": max(1, int(runs)),
        "cwd": str(resolved_cwd),
        "scenarios": {},
    }
    for scenario in scenarios:
        results["scenarios"][scenario.key] = _run_scenario(
            scenario,
            runs=int(results["runs"]),
            cwd=resolved_cwd,
        )
    return results


def compare_runtime_results(
    *,
    current_results: dict[str, Any],
    baseline_results: dict[str, Any],
    scenarios: list[RuntimeScenario],
) -> tuple[list[str], list[str]]:
    baseline_scenarios = baseline_results.get("scenarios", {})
    failures: list[str] = []
    lines = ["comparison against baseline:"]

    for scenario in scenarios:
        current_entry = current_results.get("scenarios", {}).get(scenario.key, {})
        baseline_entry = baseline_scenarios.get(scenario.key, {})
        current_median = float(current_entry.get("median_seconds", 0.0))
        baseline_median = float(baseline_entry.get("median_seconds", 0.0))
        if baseline_median <= 0:
            lines.append(f"- {scenario.key}: missing baseline median")
            failures.append(f"{scenario.key}: missing baseline")
            continue
        improvement_pct = ((baseline_median - current_median) / baseline_median) * 100.0
        status = "PASS" if improvement_pct >= scenario.target_improvement_pct else "FAIL"
        lines.append(
            f"- {scenario.key}: baseline={_format_seconds(baseline_median)} "
            f"current={_format_seconds(current_median)} "
            f"improvement={improvement_pct:.2f}% target={scenario.target_improvement_pct:.2f}% -> {status}"
        )
        if status == "FAIL":
            failures.append(
                f"{scenario.key}: expected >= {scenario.target_improvement_pct:.2f}% "
                f"got {improvement_pct:.2f}%"
            )

    return lines, failures


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _format_seconds(value: float) -> str:
    return f"{value:.3f}s"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark Smith runtime scenarios and compare to an optional baseline.",
    )
    parser.add_argument("--runs", type=int, default=3, help="Runs per scenario (default: 3)")
    parser.add_argument("--cwd", default=".", help="Working directory for command execution")
    parser.add_argument(
        "--scenarios-json",
        help="Path to the runtime benchmark scenario definition JSON. Defaults to benchmarks/runtime/scenarios.json.",
    )
    parser.add_argument(
        "--baseline-json",
        help="Path to baseline JSON for before/after delta and threshold pass/fail checks",
    )
    parser.add_argument(
        "--write-json",
        help="Write current benchmark results to JSON (use for recording a baseline)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    runs = max(1, int(args.runs))
    cwd = Path(args.cwd).resolve()
    scenarios_path = Path(args.scenarios_json).resolve() if args.scenarios_json else RUNTIME_SCENARIOS_PATH
    scenarios = load_runtime_scenarios(scenarios_path)
    if not scenarios:
        raise ValueError(f"No runtime scenarios defined in {scenarios_path}.")

    results = run_runtime_scenarios(scenarios=scenarios, runs=runs, cwd=cwd)
    results["scenarios_path"] = str(scenarios_path)

    for scenario in scenarios:
        scenario_result = results["scenarios"][scenario.key]
        print(f"{scenario.key}: median={_format_seconds(float(scenario_result['median_seconds']))}")

    if args.write_json:
        output_path = Path(args.write_json).resolve()
        _write_json(output_path, results)
        print(f"wrote results: {output_path}")

    if not args.baseline_json:
        return 0

    baseline_path = Path(args.baseline_json).resolve()
    baseline = _load_json(baseline_path)
    comparison_lines, failures = compare_runtime_results(
        current_results=results,
        baseline_results=baseline,
        scenarios=scenarios,
    )
    for line in comparison_lines:
        print(line)

    if failures:
        print("threshold check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    return 0


__all__ = [
    "RUNTIME_SCENARIOS_PATH",
    "RuntimeScenario",
    "build_parser",
    "compare_runtime_results",
    "load_runtime_scenarios",
    "main",
    "run_runtime_scenarios",
]
