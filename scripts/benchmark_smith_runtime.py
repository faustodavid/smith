#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Scenario:
    key: str
    description: str
    command: list[str]
    target_improvement_pct: float


SCENARIOS: list[Scenario] = [
    Scenario(
        key="code_search_webhook_secret",
        description='smith code search "OPENAI_WEBHOOK_SECRET" --take 20',
        command=[
            "smith",
            "code",
            "search",
            "OPENAI_WEBHOOK_SECRET",
            "--take",
            "20",
        ],
        target_improvement_pct=20.0,
    ),
    Scenario(
        key="code_grep_webhook_python",
        description='smith github code grep openai-python "OPENAI_WEBHOOK_SECRET|def unwrap|def verify_signature" --path /src/openai --output-mode files_with_matches',
        command=[
            "smith",
            "github",
            "code",
            "grep",
            "openai-python",
            "OPENAI_WEBHOOK_SECRET|def unwrap|def verify_signature",
            "--path",
            "/src/openai",
            "--output-mode",
            "files_with_matches",
        ],
        target_improvement_pct=25.0,
    ),
    Scenario(
        key="code_grep_webhook_node",
        description='smith github code grep openai-node "OPENAI_WEBHOOK_SECRET|unwrap|verifySignature" --path /src --output-mode files_with_matches',
        command=[
            "smith",
            "github",
            "code",
            "grep",
            "openai-node",
            "OPENAI_WEBHOOK_SECRET|unwrap|verifySignature",
            "--path",
            "/src",
            "--output-mode",
            "files_with_matches",
        ],
        target_improvement_pct=15.0,
    ),
]


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


def _run_scenario(scenario: Scenario, *, runs: int, cwd: Path) -> dict[str, Any]:
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


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _format_seconds(value: float) -> str:
    return f"{value:.3f}s"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark smith runtime scenarios and compare to an optional baseline.",
    )
    parser.add_argument("--runs", type=int, default=3, help="Runs per scenario (default: 3)")
    parser.add_argument("--cwd", default=".", help="Working directory for command execution")
    parser.add_argument(
        "--baseline-json",
        help="Path to baseline JSON for before/after delta and threshold pass/fail checks",
    )
    parser.add_argument(
        "--write-json",
        help="Write current benchmark results to JSON (use for recording a baseline)",
    )
    args = parser.parse_args(argv)

    runs = max(1, int(args.runs))
    cwd = Path(args.cwd).resolve()

    results: dict[str, Any] = {"runs": runs, "scenarios": {}}
    for scenario in SCENARIOS:
        scenario_result = _run_scenario(scenario, runs=runs, cwd=cwd)
        results["scenarios"][scenario.key] = scenario_result
        print(f"{scenario.key}: median={_format_seconds(scenario_result['median_seconds'])}")

    if args.write_json:
        output_path = Path(args.write_json).resolve()
        _write_json(output_path, results)
        print(f"wrote results: {output_path}")

    if not args.baseline_json:
        return 0

    baseline_path = Path(args.baseline_json).resolve()
    baseline = _load_json(baseline_path)
    baseline_scenarios = baseline.get("scenarios", {})

    failures: list[str] = []
    print("comparison against baseline:")
    for scenario in SCENARIOS:
        current_entry = results["scenarios"].get(scenario.key, {})
        baseline_entry = baseline_scenarios.get(scenario.key, {})
        current_median = float(current_entry.get("median_seconds", 0.0))
        baseline_median = float(baseline_entry.get("median_seconds", 0.0))
        if baseline_median <= 0:
            print(f"- {scenario.key}: missing baseline median")
            failures.append(f"{scenario.key}: missing baseline")
            continue
        improvement_pct = ((baseline_median - current_median) / baseline_median) * 100.0
        status = "PASS" if improvement_pct >= scenario.target_improvement_pct else "FAIL"
        print(
            f"- {scenario.key}: baseline={_format_seconds(baseline_median)} "
            f"current={_format_seconds(current_median)} "
            f"improvement={improvement_pct:.2f}% target={scenario.target_improvement_pct:.2f}% -> {status}"
        )
        if status == "FAIL":
            failures.append(
                f"{scenario.key}: expected >= {scenario.target_improvement_pct:.2f}% "
                f"got {improvement_pct:.2f}%"
            )

    if failures:
        print("threshold check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
