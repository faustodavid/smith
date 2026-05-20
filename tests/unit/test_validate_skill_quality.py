from __future__ import annotations

import importlib.util
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


def test_validate_skill_quality_classifier_examples_cover_trigger_contract() -> None:
    validator = _load_validator_module()

    assert validator.classify_trigger("Review Azure DevOps pipeline logs for build 42.") == "positive"
    assert validator.classify_trigger("Inspect GitLab merge request 42 in repo-a.") == "positive"
    assert validator.classify_trigger("Where is the webhook secret configured?") == "ambiguous"
    assert validator.classify_trigger("Create a work item for this regression.") == "negative"
    assert validator.classify_trigger("Create a GitHub issue for this bug") == "negative"


def test_evidence_path_contract_accepts_provider_repo_shapes() -> None:
    validator = _load_validator_module()

    assert validator._has_evidence_path_contract("Findings cite org/repository:path evidence.")
    assert validator._has_evidence_path_contract("GitLab answers cite group/repository:path evidence.")
    assert validator._has_evidence_path_contract("Single repo answers may cite repo:path evidence.")
    assert not validator._has_evidence_path_contract("No repository path contract is present.")


def test_fixture_resolution_prefers_script_repo_after_import_from_other_cwd(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    validator = _load_validator_module()

    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(repo_root)

    assert validator._trigger_fixture() == repo_root / "tests" / "skills" / "smith" / "fixtures" / "trigger_cases.json"
    assert validator._behavior_fixture() == repo_root / "tests" / "skills" / "smith" / "fixtures" / "behavior_cases.json"
