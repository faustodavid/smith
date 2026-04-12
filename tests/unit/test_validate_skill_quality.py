from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def _load_validator_module():
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "validate_skill_quality.py"
    spec = importlib.util.spec_from_file_location("validate_skill_quality", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_skill_quality_all_mode_passes() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "scripts/validate_skill_quality.py", "--mode", "all"],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Skill quality validation passed for mode: all" in result.stdout


def test_validate_skill_quality_classifier_examples_cover_trigger_contract() -> None:
    validator = _load_validator_module()

    assert validator.classify_trigger("Review Azure DevOps pipeline logs for build 42.") == "positive"
    assert validator.classify_trigger("Inspect GitLab merge request 42 in repo-a.") == "positive"
    assert validator.classify_trigger("Where is the webhook secret configured?") == "ambiguous"
    assert validator.classify_trigger("Create a work item for this regression.") == "negative"
    assert validator.classify_trigger("Create a GitHub issue for this bug") == "negative"


def test_behavior_fixture_covers_github_repo_shape_rule() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    fixture_path = repo_root / "tests" / "skills" / "smith" / "fixtures" / "behavior_cases.json"
    cases = json.loads(fixture_path.read_text())

    assert any(case.get("required_phrase") == "bare `<repo>`" for case in cases)
    assert any(case.get("required_phrase") == "not `org/repo`" for case in cases)
    assert any(case.get("required_phrase") == "full `group/project` paths" for case in cases)
    assert any(case.get("required_phrase") == "not a short repo name" for case in cases)
