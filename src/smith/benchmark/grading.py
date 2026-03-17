from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RepoSpec:
    env_path: str
    helper_path: str
    parse_helper: str
    verify_helper: str


REPO_SPECS: dict[str, RepoSpec] = {
    "openai/openai-python": RepoSpec(
        env_path="/src/openai/_client.py",
        helper_path="/src/openai/resources/webhooks/webhooks.py",
        parse_helper="unwrap",
        verify_helper="verify_signature",
    ),
    "openai/openai-node": RepoSpec(
        env_path="/src/client.ts",
        helper_path="/src/resources/webhooks/webhooks.ts",
        parse_helper="unwrap",
        verify_helper="verifySignature",
    ),
    "openai/openai-go": RepoSpec(
        env_path="/client.go",
        helper_path="/webhooks/webhook.go",
        parse_helper="Unwrap",
        verify_helper="VerifySignature",
    ),
    "openai/openai-ruby": RepoSpec(
        env_path="/lib/openai/client.rb",
        helper_path="/lib/openai/resources/webhooks.rb",
        parse_helper="unwrap",
        verify_helper="verify_signature",
    ),
    "openai/openai-java": RepoSpec(
        env_path="/openai-java-core/src/main/kotlin/com/openai/core/ClientOptions.kt",
        helper_path="/openai-java-core/src/main/kotlin/com/openai/services/blocking/WebhookServiceImpl.kt",
        parse_helper="unwrap",
        verify_helper="verifySignature",
    ),
}

ALLOWED_REPOS = set(REPO_SPECS)
SOURCES_HEADER_RE = re.compile(
    r"^\s*(?:#{1,6}\s+)?(?:\*\*)?Sources(?:\*\*)?:?\s*$", re.IGNORECASE | re.MULTILINE
)
SOURCE_ENTRY_RE = re.compile(r"([A-Za-z0-9._/-]+):(/?[^`\s]+)")


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
        normalized_path = path if path.startswith("/") else f"/{path}"
        normalized_repo = repo if "/" in repo else f"openai/{repo}"
        entries.append((normalized_repo, normalized_path))
    return entries


def _index_source_entries(source_entries: list[tuple[str, str]]) -> dict[str, set[str]]:
    indexed: dict[str, set[str]] = {}
    for repo, path in source_entries:
        indexed.setdefault(repo, set()).add(path)
    return indexed


def _path_in_text(answer_text: str, path: str) -> bool:
    if path in answer_text:
        return True
    without_slash = path.lstrip("/")
    return without_slash in answer_text


def _build_repo_expectations() -> list[str]:
    expectations: list[str] = []
    for repo, spec in REPO_SPECS.items():
        expectations.append(f"includes {repo}")
        expectations.append(f"includes {repo} env path {spec.env_path}")
        expectations.append(f"includes {repo} helper path {spec.helper_path}")
        expectations.append(f"{repo} helper names {spec.parse_helper} + {spec.verify_helper}")
        expectations.append(f"Sources list {repo}")
    return expectations


REPO_EXPECTATIONS = _build_repo_expectations()
SOURCES_BLOCK_EXPECTATION = "includes a Sources section with repo:path entries"
SOURCES_ONLY_EXPECTATION = "Sources include only qualifying repos"
BENCHMARK_EXPECTATIONS = [*REPO_EXPECTATIONS, SOURCES_BLOCK_EXPECTATION, SOURCES_ONLY_EXPECTATION]


def _check_repo_expectation(
    expectation: str,
    answer_text: str,
    lowered: str,
    source_index: dict[str, set[str]],
    source_repos: set[str],
) -> tuple[bool, str] | None:
    if expectation.startswith("includes "):
        remainder = expectation[len("includes ") :]
        if remainder in REPO_SPECS:
            repo = remainder
            passed = repo in lowered or repo in source_repos
            evidence = (
                f"Found {repo} in the answer." if passed else f"{repo} is missing from the answer."
            )
            return passed, evidence

        if " env path " in remainder:
            repo, path = remainder.split(" env path ", 1)
            spec = REPO_SPECS.get(repo)
            if spec:
                passed = _path_in_text(answer_text, path)
                evidence = (
                    f"Found the env file {path} for {repo}."
                    if passed
                    else f"Could not verify {path} for {repo}."
                )
                return passed, evidence

        if " helper path " in remainder:
            repo, path = remainder.split(" helper path ", 1)
            spec = REPO_SPECS.get(repo)
            if spec:
                passed = _path_in_text(answer_text, path)
                evidence = (
                    f"Found the helper file {path} for {repo}."
                    if passed
                    else f"Could not verify {path} for {repo}."
                )
                return passed, evidence

    if " helper names " in expectation:
        repo, helpers = expectation.split(" helper names ", 1)
        spec = REPO_SPECS.get(repo)
        if spec:
            passed = (spec.parse_helper.lower() in lowered) and (spec.verify_helper.lower() in lowered)
            evidence = (
                f"Found both helper names {spec.parse_helper} and {spec.verify_helper} for {repo}."
                if passed
                else f"Could not find helper names {spec.parse_helper} and {spec.verify_helper} for {repo}."
            )
            return passed, evidence

    if expectation.startswith("Sources list "):
        repo = expectation[len("Sources list ") :]
        passed = repo in source_index
        evidence = (
            f"Found {repo} in the Sources section."
            if passed
            else f"{repo} is missing from the Sources section."
        )
        return passed, evidence

    return None


def _check_sources_only_expectation(source_repos: set[str]) -> tuple[bool, str]:
    unexpected = sorted(repo for repo in source_repos if repo not in ALLOWED_REPOS)
    if not unexpected:
        return True, "Sources only list the qualifying OpenAI SDK repos."
    return False, f"Sources include unexpected repos: {', '.join(unexpected)}."


def _build_expectation_results(answer_text: str, expectations: list[str]) -> list[dict[str, Any]]:
    lowered = answer_text.lower()
    source_entries = _extract_source_entries(answer_text)
    source_index = _index_source_entries(source_entries)
    source_repos = set(source_index)

    results: list[dict[str, Any]] = []

    for expectation in expectations:
        outcome = _check_repo_expectation(expectation, answer_text, lowered, source_index, source_repos)
        if outcome is None and expectation == SOURCES_BLOCK_EXPECTATION:
            passed = bool(source_entries)
            evidence = (
                "Found repo:path entries under Sources."
                if passed
                else "No repo:path entries were found under a Sources section."
            )
        elif outcome is None and expectation == SOURCES_ONLY_EXPECTATION:
            passed, evidence = _check_sources_only_expectation(source_repos)
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
