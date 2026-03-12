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
RETENTION_VALUES_PATH = "/production/helm/loki/values.yaml"
RETENTION_DOCS_PATH = "/docs/sources/operations/storage/retention.md"
ONE_YEAR_DURATION_RE = re.compile(r"\b(?:8760h|365d|52w|1y)\b", re.IGNORECASE)
SOURCES_HEADER_RE = re.compile(
    r"^\s*(?:#{1,6}\s+)?(?:\*\*)?Sources(?:\*\*)?:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
SOURCE_ENTRY_RE = re.compile(r"((?:grafana/)?[A-Za-z0-9._-]+):(/?[^`\s]+)")


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
    entries: list[tuple[str, str]] = []
    for repo, path in SOURCE_ENTRY_RE.findall(section):
        normalized_repo = repo if repo.startswith("grafana/") else f"grafana/{repo}"
        normalized_path = path if path.startswith("/") else f"/{path}"
        entries.append((normalized_repo, normalized_path))
    return entries


def _index_source_entries(source_entries: list[tuple[str, str]]) -> dict[str, set[str]]:
    indexed: dict[str, set[str]] = {}
    for repo, path in source_entries:
        indexed.setdefault(repo, set()).add(path)
    return indexed


def _matches_config_path(answer_text: str, *parts: str) -> bool:
    dotted = re.escape(".".join(parts))
    nested_parts = [re.escape(part) for part in parts]
    nested = nested_parts[0] + r"\s*:\s*"
    for part in nested_parts[1:-1]:
        nested += r"[\s\S]{0,160}?" + part + r"\s*:\s*"
    nested += r"[\s\S]{0,160}?" + nested_parts[-1] + r"\s*:"
    return bool(re.search(rf"(?:{dotted}\b|{nested})", answer_text, re.IGNORECASE))


def _matches_one_year_retention(answer_text: str) -> bool:
    return bool(re.search(r"retention_period[\s\S]{0,80}?" + ONE_YEAR_DURATION_RE.pattern, answer_text, re.IGNORECASE))


def _mentions_delete_request_store_requirement(answer_text: str) -> bool:
    direct_order = re.search(
        r"delete_request_store[\s\S]{0,160}?"
        r"(required|required when retention is enabled|must|needs to be set|need to be set)",
        answer_text,
        re.IGNORECASE,
    )
    reverse_order = re.search(
        r"(required when retention is enabled|is required when retention is enabled|"
        r"must be set when retention is enabled|needs to be set when retention is enabled|"
        r"need to be set when retention is enabled)[\s\S]{0,160}?delete_request_store",
        answer_text,
        re.IGNORECASE,
    )
    return bool(direct_order or reverse_order)


def _recommends_table_manager_retention(answer_text: str) -> bool:
    lowered = answer_text.lower()
    for match in re.finditer(r"table[_-]?manager", lowered):
        window = lowered[max(0, match.start() - 60) : min(len(lowered), match.end() + 120)]
        if any(token in window for token in ("do not", "don't", "dont", "avoid", "instead of", "not ")):
            continue
        return True
    return False


def _check_eval0_expectation(
    expectation: str,
    answer_text: str,
    lowered: str,
    source_repos: set[str],
) -> tuple[bool, str] | None:
    if expectation == "includes grafana/alloy":
        passed = "grafana/alloy" in lowered or "grafana/alloy" in source_repos
        evidence = "Found grafana/alloy in final answer." if passed else "grafana/alloy is missing from the final answer."
        return passed, evidence
    if expectation == "includes grafana/agent":
        passed = "grafana/agent" in lowered or "grafana/agent" in source_repos
        evidence = "Found grafana/agent in final answer." if passed else "grafana/agent is missing from the final answer."
        return passed, evidence
    if expectation == "includes internal/component/otelcol/exporter/loki/loki.go":
        target_path = "/internal/component/otelcol/exporter/loki/loki.go"
        passed = target_path in answer_text or target_path.removeprefix("/") in answer_text
        evidence = f"Found {target_path}." if passed else f"{target_path} is missing from the final answer."
        return passed, evidence
    if expectation == 'maps grafana/alloy to alloy:"forward_to,attr"':
        target = EXPECTED_IMPLEMENTATIONS["grafana/alloy"]["tag"]
        passed = bool(
            re.search(rf"(?:grafana/)?alloy[\s\S]{{0,300}}{re.escape(target)}", answer_text, re.IGNORECASE)
        )
        evidence = f"grafana/alloy is paired with `{target}`." if passed else f"Could not verify grafana/alloy paired with `{target}`."
        return passed, evidence
    if expectation == 'maps grafana/agent to river:"forward_to,attr"':
        target = EXPECTED_IMPLEMENTATIONS["grafana/agent"]["tag"]
        passed = bool(
            re.search(rf"(?:grafana/)?agent[\s\S]{{0,300}}{re.escape(target)}", answer_text, re.IGNORECASE)
        )
        evidence = f"grafana/agent is paired with `{target}`." if passed else f"Could not verify grafana/agent paired with `{target}`."
        return passed, evidence
    if expectation == "does not present docs-only hits as implementation repos":
        unexpected = sorted(repo for repo in source_repos if repo in DOCS_ONLY_REPOS or repo not in EXPECTED_IMPLEMENTATIONS)
        passed = not unexpected
        evidence = (
            "Sources list only implementation repositories."
            if passed
            else f"Sources include non-implementation repositories: {', '.join(unexpected)}."
        )
        return passed, evidence
    return None


def _check_eval1_expectation(
    expectation: str,
    answer_text: str,
    lowered: str,
    source_index: dict[str, set[str]],
) -> tuple[bool, str] | None:
    if expectation == "includes grafana/loki":
        passed = "grafana/loki" in lowered or "grafana/loki" in source_index
        evidence = "Found grafana/loki in final answer." if passed else "grafana/loki is missing from the final answer."
        return passed, evidence
    if expectation == "includes loki.limits_config.retention_period":
        passed = _matches_config_path(answer_text, "loki", "limits_config", "retention_period")
        evidence = (
            "Found the Loki Helm key path for retention_period."
            if passed
            else "Could not verify the Loki Helm key path `loki.limits_config.retention_period`."
        )
        return passed, evidence
    if expectation == "maps retention_period to 1 year":
        passed = _matches_one_year_retention(answer_text)
        evidence = (
            "retention_period is paired with a one-year duration."
            if passed
            else "Could not verify a one-year duration next to retention_period."
        )
        return passed, evidence
    if expectation == "includes loki.compactor.retention_enabled":
        passed = _matches_config_path(answer_text, "loki", "compactor", "retention_enabled")
        evidence = (
            "Found the Loki Helm key path for compactor retention_enabled."
            if passed
            else "Could not verify the Loki Helm key path `loki.compactor.retention_enabled`."
        )
        return passed, evidence
    if expectation == "includes loki.compactor.delete_request_store":
        passed = _matches_config_path(answer_text, "loki", "compactor", "delete_request_store")
        evidence = (
            "Found the Loki Helm key path for compactor delete_request_store."
            if passed
            else "Could not verify the Loki Helm key path `loki.compactor.delete_request_store`."
        )
        return passed, evidence
    if expectation == "states delete_request_store is required when retention is enabled":
        passed = _mentions_delete_request_store_requirement(answer_text)
        evidence = (
            "delete_request_store is described as required when retention is enabled."
            if passed
            else "Could not verify that delete_request_store is described as required when retention is enabled."
        )
        return passed, evidence
    if expectation == "does not recommend deprecated table_manager retention":
        passed = not _recommends_table_manager_retention(answer_text)
        evidence = (
            "The answer does not recommend the deprecated table_manager retention path."
            if passed
            else "The answer recommends the deprecated table_manager retention path."
        )
        return passed, evidence
    if expectation == "includes sources for values.yaml and retention.md":
        grafana_loki_sources = source_index.get("grafana/loki", set())
        missing = sorted(path for path in (RETENTION_VALUES_PATH, RETENTION_DOCS_PATH) if path not in grafana_loki_sources)
        passed = not missing
        evidence = (
            "Found grafana/loki sources for the chart values file and retention docs."
            if passed
            else f"Missing required grafana/loki source paths: {', '.join(missing)}."
        )
        return passed, evidence
    return None


def _build_expectation_results(answer_text: str, expectations: list[str]) -> list[dict[str, Any]]:
    lowered = answer_text.lower()
    source_entries = _extract_source_entries(answer_text)
    source_index = _index_source_entries(source_entries)
    source_repos = set(source_index)

    results: list[dict[str, Any]] = []

    for expectation in expectations:
        outcome = _check_eval0_expectation(expectation, answer_text, lowered, source_repos)
        if outcome is None:
            outcome = _check_eval1_expectation(expectation, answer_text, lowered, source_index)

        if outcome is None and expectation == "includes a Sources section with repo:path":
            passed = bool(source_entries)
            evidence = (
                "Found repo:path entries under Sources."
                if passed
                else "No repo:path entries were found under a Sources section."
            )
        elif outcome is None:
            passed = False
            evidence = "No deterministic grader is defined for this expectation."
        else:
            passed, evidence = outcome

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
