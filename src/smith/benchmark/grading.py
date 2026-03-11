from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

EXPECTED_IMPLEMENTATIONS = {
    "grafana/alloy": {
        "path": "/internal/component/otelcol/exporter/loki/loki.go",
        "tag": 'alloy:"forward_to,attr"',
    },
    "grafana/agent": {
        "path": "/internal/component/otelcol/exporter/loki/loki.go",
        "tag": 'river:"forward_to,attr"',
    },
}
DOCS_ONLY_REPOS = {"grafana/loki"}
SOURCES_HEADER_RE = re.compile(r"^#{1,6}\s+Sources\s*$", re.IGNORECASE | re.MULTILINE)
SOURCE_ENTRY_RE = re.compile(r"(grafana/[A-Za-z0-9._-]+):(/[^`\s]+)")


def _load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _extract_sources_section(answer_text: str) -> str:
    match = SOURCES_HEADER_RE.search(answer_text)
    if not match:
        return ""
    return answer_text[match.end() :].strip()


def _extract_source_entries(answer_text: str) -> list[tuple[str, str]]:
    section = _extract_sources_section(answer_text)
    return [(repo, path) for repo, path in SOURCE_ENTRY_RE.findall(section)]


def _build_expectation_results(answer_text: str, expectations: list[str]) -> list[dict[str, Any]]:
    lowered = answer_text.lower()
    source_entries = _extract_source_entries(answer_text)
    source_map = {repo: path for repo, path in source_entries}
    source_repos = set(source_map)

    results: list[dict[str, Any]] = []

    for expectation in expectations:
        if expectation == "includes grafana/alloy":
            passed = "grafana/alloy" in lowered
            evidence = "Found grafana/alloy in final answer." if passed else "grafana/alloy is missing from the final answer."
        elif expectation == "includes grafana/agent":
            passed = "grafana/agent" in lowered
            evidence = "Found grafana/agent in final answer." if passed else "grafana/agent is missing from the final answer."
        elif expectation == "includes internal/component/otelcol/exporter/loki/loki.go":
            target_path = "/internal/component/otelcol/exporter/loki/loki.go"
            passed = target_path in answer_text
            evidence = f"Found {target_path}." if passed else f"{target_path} is missing from the final answer."
        elif expectation == 'maps grafana/alloy to alloy:"forward_to,attr"':
            target = EXPECTED_IMPLEMENTATIONS["grafana/alloy"]["tag"]
            passed = bool(re.search(rf"grafana/alloy[\s\S]{{0,300}}{re.escape(target)}", answer_text, re.IGNORECASE))
            evidence = f"grafana/alloy is paired with `{target}`." if passed else f"Could not verify grafana/alloy paired with `{target}`."
        elif expectation == 'maps grafana/agent to river:"forward_to,attr"':
            target = EXPECTED_IMPLEMENTATIONS["grafana/agent"]["tag"]
            passed = bool(re.search(rf"grafana/agent[\s\S]{{0,300}}{re.escape(target)}", answer_text, re.IGNORECASE))
            evidence = f"grafana/agent is paired with `{target}`." if passed else f"Could not verify grafana/agent paired with `{target}`."
        elif expectation == "does not present docs-only hits as implementation repos":
            unexpected = sorted(repo for repo in source_repos if repo in DOCS_ONLY_REPOS or repo not in EXPECTED_IMPLEMENTATIONS)
            passed = not unexpected
            evidence = (
                "Sources list only implementation repositories."
                if passed
                else f"Sources include non-implementation repositories: {', '.join(unexpected)}."
            )
        elif expectation == "includes a Sources section with repo:path":
            passed = bool(source_entries)
            evidence = (
                "Found repo:path entries under Sources."
                if passed
                else "No repo:path entries were found under a Sources section."
            )
        else:
            passed = False
            evidence = "No deterministic grader is defined for this expectation."

        results.append({"text": expectation, "passed": passed, "evidence": evidence})

    return results


def build_grading_result(
    *,
    answer_text: str,
    expectations: list[str],
    metrics: dict[str, Any] | None = None,
    timing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expectation_results = _build_expectation_results(answer_text, expectations)
    passed = sum(1 for item in expectation_results if item["passed"])
    total = len(expectation_results)
    failed = total - passed

    return {
        "expectations": expectation_results,
        "summary": {
            "passed": passed,
            "failed": failed,
            "total": total,
            "pass_rate": round((passed / total) if total else 0.0, 4),
        },
        "execution_metrics": metrics or {},
        "timing": timing or {},
        "claims": [],
        "user_notes_summary": {
            "uncertainties": [],
            "needs_review": [],
            "workarounds": [],
        },
        "eval_feedback": {
            "suggestions": [],
            "overall": "No suggestions, evals look solid",
        },
    }


def grade_run_directory(run_dir: Path, expectations: list[str]) -> dict[str, Any]:
    outputs_dir = run_dir / "outputs"
    answer_text = (outputs_dir / "final_answer.md").read_text()
    metrics = _load_optional_json(outputs_dir / "metrics.json")
    timing = _load_optional_json(run_dir / "timing.json")
    grading = build_grading_result(
        answer_text=answer_text,
        expectations=expectations,
        metrics=metrics,
        timing=timing,
    )
    (run_dir / "grading.json").write_text(json.dumps(grading, indent=2, sort_keys=True) + "\n")
    return grading
