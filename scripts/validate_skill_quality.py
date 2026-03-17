#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL_MD = SKILL_DIR / "SKILL.md"
USAGE_RECIPES = SKILL_DIR / "references" / "usage-recipes.md"
AUTH_TROUBLE = SKILL_DIR / "references" / "auth-troubleshooting.md"
TRIGGER_CASES_DOC = SKILL_DIR / "references" / "trigger-cases.md"
BEHAVIOR_GATES_DOC = SKILL_DIR / "references" / "behavioral-quality-gates.md"
FAILURE_PLAYBOOK_DOC = SKILL_DIR / "references" / "failure-playbook.md"
CLAUDE_COMMANDS_DIR = SKILL_DIR / "assets" / "claude-commands"

def _candidate_roots() -> list[Path]:
    candidates: list[Path] = []

    env_root = None
    for key in ("SMITH_REPO_ROOT", "PROJECT_ROOT", "REPO_ROOT"):
        value = (Path.cwd() / "").__class__  # satisfy pyright about Path import use
        del value
        raw = __import__("os").getenv(key)
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
    for root in _candidate_roots():
        path = root / "tests" / "skills" / "smith" / "fixtures" / filename
        if path.exists():
            return path
    # Return the most likely repo path for clear error output
    return Path.home() / "Documents" / "projects" / "smith" / "tests" / "skills" / "smith" / "fixtures" / filename


TRIGGER_FIXTURE = _resolve_fixture("trigger_cases.json")
BEHAVIOR_FIXTURE = _resolve_fixture("behavior_cases.json")


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


def run_trigger_checks() -> list[str]:
    errors: list[str] = []

    required_paths = [SKILL_MD, TRIGGER_CASES_DOC, TRIGGER_FIXTURE]
    for path in required_paths:
        if not path.exists():
            errors.append(f"Missing required trigger artifact: {path}")
            return errors

    skill_text = _read(SKILL_MD)
    frontmatter = _extract_frontmatter(skill_text)
    description = frontmatter.get("description", "")

    if "Use when" not in description:
        errors.append("Frontmatter description is missing positive trigger phrasing ('Use when').")
    if "Do not use" not in description:
        errors.append("Frontmatter description is missing anti-trigger phrasing ('Do not use').")

    required_sections = [
        "## Trigger Decision",
        "### Use smith when",
        "### Do not use smith when",
        "### Ambiguous request fallback",
    ]
    for section in required_sections:
        if section not in skill_text:
            errors.append(f"SKILL.md missing section: {section}")

    explicit_invocation_markers = [
        "smith code search",
        "smith orgs github",
        "smith repos github",
        "code grep github <repo>",
        "code grep azdo <project> <repo>",
        "prs list github <repo>",
        "pipelines logs list github <repo> <id>",
        "stories search azdo <project> --query",
    ]
    for marker in explicit_invocation_markers:
        if marker not in skill_text:
            errors.append(f"SKILL.md missing explicit invocation marker: {marker}")

    trigger_cases = _load_json(TRIGGER_FIXTURE)
    if not isinstance(trigger_cases, list) or not trigger_cases:
        errors.append("trigger_cases.json must contain a non-empty array.")
        return errors

    for case in trigger_cases:
        prompt = str(case.get("prompt", "")).strip()
        expected = str(case.get("expected", "")).strip()
        if not prompt or expected not in {"positive", "negative", "ambiguous"}:
            errors.append(f"Invalid trigger case: {case}")
            continue
        predicted = classify_trigger(prompt)
        if predicted != expected:
            errors.append(
                f"Trigger classification mismatch for '{prompt}': expected '{expected}', got '{predicted}'"
            )

    return errors


def run_behavior_checks() -> list[str]:
    errors: list[str] = []

    required_files = [
        SKILL_MD,
        USAGE_RECIPES,
        AUTH_TROUBLE,
        BEHAVIOR_GATES_DOC,
        FAILURE_PLAYBOOK_DOC,
        BEHAVIOR_FIXTURE,
    ]
    for path in required_files:
        if not path.exists():
            errors.append(f"Missing required behavior artifact: {path}")
            return errors

    skill_text = _read(SKILL_MD)
    recipes_text = _read(USAGE_RECIPES)
    failure_text = _read(FAILURE_PLAYBOOK_DOC)

    required_skill_sections = [
        "## Investigation Algorithm",
        "## Stop Conditions",
        "## Failure Handling Flow",
    ]
    for section in required_skill_sections:
        if section not in skill_text:
            errors.append(f"SKILL.md missing behavioral section: {section}")

    recovery_terms = ["401 or 403", "429", "Truncation", "Empty results", "Wrong repository"]
    for term in recovery_terms:
        if term not in skill_text and term not in failure_text:
            errors.append(f"Recovery flow missing term: {term}")

    command_markers = [
        "smith orgs azdo",
        "smith orgs github",
        "smith repos azdo <project>",
        "smith repos github",
        "smith code search",
        "smith code grep azdo <project> <repo>",
        "smith code grep github <repo>",
        "smith prs list azdo <project> <repo>",
        "smith prs get github <repo> <id>",
        "smith prs threads azdo <project> <repo> <id>",
        "smith pipelines logs list azdo <project> <id>",
        "smith pipelines logs grep github <repo> <id>",
        "smith stories get azdo <project> <id>",
        "smith stories search github <repo> --query",
        "smith stories mine azdo <project>",
    ]
    for marker in command_markers:
        if marker not in recipes_text and marker not in skill_text:
            errors.append(f"Command coverage missing marker: {marker}")

    templates = sorted(CLAUDE_COMMANDS_DIR.glob("smith-*.md"))
    if len(templates) < 5:
        errors.append("Expected at least 5 Claude command templates.")
    required_template_terms = [
        "Preflight",
        "Required arguments",
        "First command to run",
        "If no results",
        "Output contract",
        "read-only",
        "smith_cli.py",
    ]
    for template in templates:
        content = _read(template)
        for term in required_template_terms:
            if term not in content:
                errors.append(f"Template {template.name} missing '{term}'")
        if not _has_evidence_path_contract(content):
            errors.append(f"Template {template.name} missing evidence-path requirement")

    behavior_cases = _load_json(BEHAVIOR_FIXTURE)
    if not isinstance(behavior_cases, list) or not behavior_cases:
        errors.append("behavior_cases.json must contain a non-empty array.")
        return errors

    combined_text = "\n".join([skill_text, recipes_text, failure_text])
    for case in behavior_cases:
        case_name = str(case.get("name", "")).strip() or "unnamed_case"

        expected_sequence = case.get("expected_sequence")
        if expected_sequence is not None:
            if not isinstance(expected_sequence, list) or not expected_sequence:
                errors.append(f"Behavior case '{case_name}' has invalid expected_sequence")
            else:
                for marker in expected_sequence:
                    marker_text = str(marker).strip()
                    if marker_text and marker_text not in combined_text:
                        errors.append(
                            f"Behavior case '{case_name}' missing sequence marker '{marker_text}' in docs"
                        )

        required_phrase = case.get("required_phrase")
        if required_phrase is not None:
            phrase = str(required_phrase).strip()
            if phrase and phrase not in combined_text:
                errors.append(
                    f"Behavior case '{case_name}' requires phrase '{phrase}' not found"
                )

        requires_evidence_paths = case.get("requires_evidence_paths")
        if requires_evidence_paths is True and not _has_evidence_path_contract(combined_text):
            errors.append(
                f"Behavior case '{case_name}' requires evidence path contract, but contract not found"
            )

    return errors


def _has_evidence_path_contract(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "project/repository:path",
            "org/repository:path",
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
