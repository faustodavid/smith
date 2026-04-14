from __future__ import annotations

import json
from pathlib import Path

import pytest

from smith.benchmark.runtime import (
    RuntimeScenario,
    compare_runtime_results,
    load_runtime_scenarios,
    run_runtime_scenarios,
)


def test_load_runtime_scenarios_reads_json_payload(tmp_path: Path) -> None:
    scenarios_path = tmp_path / "scenarios.json"
    scenarios_path.write_text(
        json.dumps(
            {
                "scenarios": [
                    {
                        "key": "code_search",
                        "description": "smith code search \"needle\"",
                        "command": ["smith", "code", "search", "needle"],
                        "target_improvement_pct": 10,
                    }
                ]
            }
        )
    )

    scenarios = load_runtime_scenarios(scenarios_path)

    assert scenarios == [
        RuntimeScenario(
            key="code_search",
            description='smith code search "needle"',
            command=["smith", "code", "search", "needle"],
            target_improvement_pct=10.0,
        )
    ]


def test_run_runtime_scenarios_collects_samples_and_median(tmp_path: Path, monkeypatch) -> None:
    samples = iter([0.3, 0.5, 0.4])

    def fake_run_command(command: list[str], *, cwd: Path) -> float:
        assert command == ["smith", "code", "search", "needle"]
        assert cwd == tmp_path.resolve()
        return next(samples)

    monkeypatch.setattr("smith.benchmark.runtime._run_command", fake_run_command)
    scenario = RuntimeScenario(
        key="code_search",
        description='smith code search "needle"',
        command=["smith", "code", "search", "needle"],
        target_improvement_pct=10.0,
    )

    results = run_runtime_scenarios(scenarios=[scenario], runs=3, cwd=tmp_path)

    assert results["cwd"] == str(tmp_path.resolve())
    assert results["runs"] == 3
    assert results["scenarios"]["code_search"]["samples_seconds"] == [0.3, 0.5, 0.4]
    assert results["scenarios"]["code_search"]["median_seconds"] == pytest.approx(0.4)


def test_compare_runtime_results_reports_threshold_failures() -> None:
    scenario = RuntimeScenario(
        key="code_search",
        description='smith code search "needle"',
        command=["smith", "code", "search", "needle"],
        target_improvement_pct=20.0,
    )

    lines, failures = compare_runtime_results(
        current_results={"scenarios": {"code_search": {"median_seconds": 0.9}}},
        baseline_results={"scenarios": {"code_search": {"median_seconds": 1.0}}},
        scenarios=[scenario],
    )

    assert lines[0] == "comparison against baseline:"
    assert lines[1].endswith("-> FAIL")
    assert failures == ["code_search: expected >= 20.00% got 10.00%"]
