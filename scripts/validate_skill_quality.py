#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skills" / "smith"
SKILL_MD = SKILL_DIR / "SKILL.md"
USAGE_RECIPES = SKILL_DIR / "references" / "usage-recipes.md"
AUTH_TROUBLE = SKILL_DIR / "references" / "auth-troubleshooting.md"
TRIGGER_CASES_DOC = SKILL_DIR / "references" / "trigger-cases.md"
BEHAVIOR_GATES_DOC = SKILL_DIR / "references" / "behavioral-quality-gates.md"
FAILURE_PLAYBOOK_DOC = SKILL_DIR / "references" / "failure-playbook.md"
TOKEN_ENV_VARS = (
    "GITHUB_TOKEN",
    "GITLAB_TOKEN",
    "AZURE_DEVOPS_PAT",
    "YOUTRACK_TOKEN",
)


def _candidate_roots() -> list[Path]:
    candidates: list[Path] = []

    env_root = None
    for key in ("SMITH_REPO_ROOT", "PROJECT_ROOT", "REPO_ROOT"):
        raw = os.getenv(key)
        if raw:
            env_root = Path(raw).expanduser().resolve()
            candidates.append(env_root)

    cwd = Path.cwd().resolve()
    candidates.extend([cwd, *cwd.parents])

    this_file = Path(__file__).resolve()
    candidates.extend([this_file.parent, *this_file.parents])

    home = Path.home()
    candidates.append((home / "Documents" / "projects" / "smith").resolve())

    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        unique.append(candidate)
    return unique


def _resolve_fixture(filename: str) -> Path:
    repo_fixture = REPO_ROOT / "tests" / "skills" / "smith" / "fixtures" / filename
    if repo_fixture.exists():
        return repo_fixture

    for root in _candidate_roots():
        path = root / "tests" / "skills" / "smith" / "fixtures" / filename
        if path.exists():
            return path
    # Return the most likely repo path for clear error output
    return Path.home() / "Documents" / "projects" / "smith" / "tests" / "skills" / "smith" / "fixtures" / filename


def _trigger_fixture() -> Path:
    return _resolve_fixture("trigger_cases.json")


def _behavior_fixture() -> Path:
    return _resolve_fixture("behavior_cases.json")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _extract_frontmatter(skill_text: str) -> dict[str, str]:
    match = re.match(r"^---\n(.*?)\n---\n", skill_text, re.DOTALL)
    if not match:
        return {}

    result: dict[str, str] = {}
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip().strip('"')
    return result


def classify_trigger(prompt: str) -> str:
    text = prompt.lower()

    write_targets = r"(?:work\s+item|github\s+issue|issue|ticket|story|pr|pull\s+request|merge\s+request)"
    write_patterns = [
        rf"\bcreate\b.+\b{write_targets}\b",
        rf"\bupdate\b.+\b{write_targets}\b",
        r"\bapprove\b.+\b(?:pr|pull\s+request|merge\s+request)\b",
        rf"\bcomment\s+on\b.+\b{write_targets}\b",
        rf"\bclose\b.+\b{write_targets}\b",
    ]
    if any(re.search(pattern, text) for pattern in write_patterns):
        return "negative"

    negative_terms = [
        "create work item",
        "create a work item",
        "create issue",
        "create a github issue",
        "create github issue",
        "update work item",
        "update a work item",
        "update issue",
        "comment on",
        "approve",
        "approve pr",
        "approve pull request",
        "post slack",
        "slack",
        "public internet",
        "public web",
        "search the web",
        "internet docs",
        "web docs",
        "creative",
    ]
    if any(term in text for term in negative_terms):
        return "negative"

    ambiguous_terms = [
        "where is x configured",
        "where is",
        "find the ttl setting",
        "investigate this failure",
        "investigate why",
    ]
    if any(term in text for term in ambiguous_terms):
        return "ambiguous"

    positive_terms = [
        "repo",
        "repository",
        "search code",
        "grep",
        "loki",
        "github",
        "gitlab",
        "build failure",
        "build log",
        "build logs",
        "pipeline log",
        "pipeline failure",
        "pipeline",
        "pipeline logs",
        "pipelines logs",
        "pr ",
        "prs ",
        "pull request",
        "merge request",
        "issue",
        "stories",
        "work items",
        "azure devops",
        "configured",
        "terraform",
    ]
    if any(term in text for term in positive_terms):
        return "positive"

    return "ambiguous"


def _missing_required_artifact_errors(paths: list[Path], label: str) -> list[str]:
    for path in paths:
        if not path.exists():
            return [f"Missing required {label} artifact: {path}"]
    return []


def _missing_marker_errors(text: str, markers: list[str], template: str) -> list[str]:
    return [template.format(marker=marker) for marker in markers if marker not in text]


def _strip_markdown_command_prefix(line: str) -> str:
    stripped = line.strip()
    while stripped.startswith(">"):
        stripped = stripped[1:].lstrip()
    while len(stripped) >= 2 and stripped[:2] in {"- ", "* ", "+ "}:
        stripped = stripped[2:].lstrip()
    if stripped.startswith("$ "):
        stripped = stripped[2:].lstrip()
    return stripped


def _validate_auth_troubleshooting_token_safety(auth_text: str) -> list[str]:
    token_names = "|".join(re.escape(token) for token in TOKEN_ENV_VARS)
    unsafe_patterns = [
        re.compile(r"^\s*(?:env|printenv|set)(?:\s*(?:\||>|$))"),
        re.compile(rf"^\s*printenv\s+({token_names})(?:\s|$)"),
        re.compile(rf"^\s*echo\b.*\$(?:{{)?({token_names})(?::-[^}}]*)?(?:}})?"),
        re.compile(rf"^\s*printf\b.*\$(?:{{)?({token_names})(?::-[^}}]*)?(?:}})?"),
        re.compile(rf"^\s*(?:env|printenv)\b.*\|\s*grep\b.*(?:{token_names})"),
    ]

    errors: list[str] = []
    for line_no, line in enumerate(auth_text.splitlines(), start=1):
        command = _strip_markdown_command_prefix(line)
        if command.startswith("#"):
            continue
        if any(pattern.search(command) for pattern in unsafe_patterns):
            errors.append(f"Auth troubleshooting must not print token values: line {line_no}")
    return errors


def _validate_trigger_frontmatter(skill_text: str, description: str) -> list[str]:
    errors: list[str] = []
    if "Use when" not in description:
        errors.append("Frontmatter description is missing positive trigger phrasing ('Use when').")
    if "Do not use" not in description and "### Do not use smith when" not in skill_text:
        errors.append("Frontmatter description is missing anti-trigger phrasing ('Do not use').")
    return errors


def _validate_trigger_case(case: Any) -> list[str]:
    prompt = str(case.get("prompt", "")).strip()
    expected = str(case.get("expected", "")).strip()
    if not prompt or expected not in {"positive", "negative", "ambiguous"}:
        return [f"Invalid trigger case: {case}"]

    predicted = classify_trigger(prompt)
    if predicted != expected:
        return [f"Trigger classification mismatch for '{prompt}': expected '{expected}', got '{predicted}'"]

    return []


def _validate_trigger_cases(trigger_cases: Any) -> list[str]:
    if not isinstance(trigger_cases, list) or not trigger_cases:
        return ["trigger_cases.json must contain a non-empty array."]

    errors: list[str] = []
    for case in trigger_cases:
        errors.extend(_validate_trigger_case(case))
    return errors


def run_trigger_checks() -> list[str]:
    errors: list[str] = []
    trigger_fixture = _trigger_fixture()

    required_paths = [SKILL_MD, USAGE_RECIPES, TRIGGER_CASES_DOC, trigger_fixture]
    errors.extend(_missing_required_artifact_errors(required_paths, "trigger"))
    if errors:
        return errors

    skill_text = _read(SKILL_MD)
    recipes_text = _read(USAGE_RECIPES)
    combined_text = "\n".join([skill_text, recipes_text])
    frontmatter = _extract_frontmatter(skill_text)
    description = frontmatter.get("description", "")
    errors.extend(_validate_trigger_frontmatter(skill_text, description))

    required_sections = [
        "## Trigger Decision",
        "### Use smith when",
        "### Do not use smith when",
        "### Ambiguous request fallback",
    ]
    errors.extend(_missing_marker_errors(skill_text, required_sections, "SKILL.md missing section: {marker}"))

    explicit_invocation_markers = [
        "smith code search",
        "smith <azdo-remote-name> code search",
        "smith <github-remote-name> code search",
        "smith <gitlab-remote-name> code search",
        "smith <azdo-remote-name> orgs",
        "smith <github-remote-name> orgs",
        "smith <gitlab-remote-name> groups",
        "smith <azdo-remote-name> repos <project>",
        "smith <github-remote-name> repos",
        "smith <gitlab-remote-name> repos",
        "smith <azdo-remote-name> code grep <project> <repo>",
        "smith <github-remote-name> code grep <repo>",
        "smith <gitlab-remote-name> code grep <group/project>",
        "smith prs search",
        "smith <azdo-remote-name> prs search",
        "smith <github-remote-name> prs search",
        "smith <gitlab-remote-name> prs search",
        "smith <github-remote-name> prs list <repo>",
        "smith <gitlab-remote-name> prs list <group/project>",
        "smith <github-remote-name> pipelines list <repo> <id>",
        "smith <gitlab-remote-name> pipelines list <group/project> <id>",
        "smith <github-remote-name> pipelines grep <repo> <id>",
        "smith <gitlab-remote-name> pipelines grep <group/project> <id>",
        "smith <azdo-remote-name> stories search <project> --query",
        "smith <gitlab-remote-name> stories search <group/project> --query",
    ]
    errors.extend(
        _missing_marker_errors(combined_text, explicit_invocation_markers, "SKILL.md missing explicit invocation marker: {marker}")
    )

    trigger_cases = _load_json(trigger_fixture)
    errors.extend(_validate_trigger_cases(trigger_cases))

    return errors


def _validate_behavior_case_sequence(case_name: str, expected_sequence: Any, combined_text: str) -> list[str]:
    if expected_sequence is None:
        return []
    if not isinstance(expected_sequence, list) or not expected_sequence:
        return [f"Behavior case '{case_name}' has invalid expected_sequence"]

    errors: list[str] = []
    for marker in expected_sequence:
        marker_text = str(marker).strip()
        if marker_text and marker_text not in combined_text:
            errors.append(f"Behavior case '{case_name}' missing sequence marker '{marker_text}' in docs")
    return errors


def _validate_behavior_case_phrase(case_name: str, required_phrase: Any, combined_text: str) -> list[str]:
    if required_phrase is None:
        return []

    phrase = str(required_phrase).strip()
    if phrase and phrase not in combined_text:
        return [f"Behavior case '{case_name}' requires phrase '{phrase}' not found"]

    return []


def _validate_behavior_case_evidence(case_name: str, requires_evidence_paths: Any, combined_text: str) -> list[str]:
    if requires_evidence_paths is True and not _has_evidence_path_contract(combined_text):
        return [f"Behavior case '{case_name}' requires evidence path contract, but contract not found"]

    return []


def _validate_behavior_case(case: Any, combined_text: str) -> list[str]:
    case_name = str(case.get("name", "")).strip() or "unnamed_case"

    errors: list[str] = []
    errors.extend(_validate_behavior_case_sequence(case_name, case.get("expected_sequence"), combined_text))
    errors.extend(_validate_behavior_case_phrase(case_name, case.get("required_phrase"), combined_text))
    errors.extend(_validate_behavior_case_evidence(case_name, case.get("requires_evidence_paths"), combined_text))
    return errors


def _validate_behavior_cases(behavior_cases: Any, combined_text: str) -> list[str]:
    if not isinstance(behavior_cases, list) or not behavior_cases:
        return ["behavior_cases.json must contain a non-empty array."]

    errors: list[str] = []
    for case in behavior_cases:
        errors.extend(_validate_behavior_case(case, combined_text))
    return errors


def run_behavior_checks() -> list[str]:
    errors: list[str] = []
    behavior_fixture = _behavior_fixture()

    required_files = [
        SKILL_MD,
        USAGE_RECIPES,
        AUTH_TROUBLE,
        BEHAVIOR_GATES_DOC,
        FAILURE_PLAYBOOK_DOC,
        behavior_fixture,
    ]
    errors.extend(_missing_required_artifact_errors(required_files, "behavior"))
    if errors:
        return errors

    skill_text = _read(SKILL_MD)
    recipes_text = _read(USAGE_RECIPES)
    auth_text = _read(AUTH_TROUBLE)
    failure_text = _read(FAILURE_PLAYBOOK_DOC)
    errors.extend(_validate_auth_troubleshooting_token_safety(auth_text))

    required_skill_sections = [
        "## Investigation Algorithm",
        "## Stop Conditions",
        "## Failure Handling",
    ]
    errors.extend(_missing_marker_errors(skill_text, required_skill_sections, "SKILL.md missing behavioral section: {marker}"))

    recovery_terms = ["401 or 403", "429", "Truncation", "Empty results", "Wrong repository"]
    errors.extend(f"Recovery flow missing term: {term}" for term in recovery_terms if term not in skill_text and term not in failure_text)

    command_markers = [
        "smith <azdo-remote-name> orgs",
        "smith <github-remote-name> orgs",
        "smith <gitlab-remote-name> groups",
        "smith <azdo-remote-name> repos <project>",
        "smith <github-remote-name> repos",
        "smith <gitlab-remote-name> repos",
        "smith code search",
        "smith <azdo-remote-name> code grep <project> <repo>",
        "smith <github-remote-name> code grep <repo>",
        "smith <gitlab-remote-name> code grep <group/project>",
        "smith prs search",
        "smith <azdo-remote-name> prs search",
        "smith <github-remote-name> prs search",
        "smith <gitlab-remote-name> prs search",
        "smith <azdo-remote-name> prs list <project> <repo>",
        "smith <github-remote-name> prs get <repo> <id>",
        "smith <gitlab-remote-name> prs get <group/project> <id>",
        "smith <azdo-remote-name> prs threads <project> <repo> <id>",
        "smith <azdo-remote-name> pipelines list <project> <id>",
        "smith <github-remote-name> pipelines list <repo> <id>",
        "smith <gitlab-remote-name> pipelines list <group/project> <id>",
        "smith <azdo-remote-name> pipelines grep <project> <id>",
        "smith <github-remote-name> pipelines grep <repo> <id>",
        "smith <gitlab-remote-name> pipelines grep <group/project> <id>",
        "smith <azdo-remote-name> stories get <project> <id>",
        "smith <github-remote-name> stories search <repo> --query",
        "smith <gitlab-remote-name> stories search <group/project> --query",
        "smith <azdo-remote-name> stories mine <project>",
        "smith <gitlab-remote-name> stories mine <group/project>",
    ]
    command_text = "\n".join([recipes_text, skill_text])
    errors.extend(_missing_marker_errors(command_text, command_markers, "Command coverage missing marker: {marker}"))

    behavior_cases = _load_json(behavior_fixture)
    combined_text = "\n".join([skill_text, recipes_text, failure_text])
    errors.extend(_validate_behavior_cases(behavior_cases, combined_text))

    return errors


def _has_evidence_path_contract(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "project/repository:path",
            "org/repository:path",
            "group/repository:path",
            "repo:path",
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate smith skill quality gates")
    parser.add_argument(
        "--mode",
        choices=["trigger", "behavior", "all"],
        required=True,
        help="Validation mode",
    )
    args = parser.parse_args(argv)

    all_errors: list[str] = []
    if args.mode in {"trigger", "all"}:
        all_errors.extend(run_trigger_checks())
    if args.mode in {"behavior", "all"}:
        all_errors.extend(run_behavior_checks())

    if all_errors:
        print("Skill quality validation failed:")
        for error in all_errors:
            print(f"- {error}")
        return 1

    print(f"Skill quality validation passed for mode: {args.mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
