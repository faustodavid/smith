from __future__ import annotations

import re

import pytest

from smith.errors import SmithError
from smith.providers import local_checkout


def test_ripgrep_local_result_preserves_hidden_paths_and_single_file_targets(tmp_path) -> None:
    hidden_dir = tmp_path / ".github" / "workflows"
    hidden_dir.mkdir(parents=True)
    workflow = hidden_dir / "ci.yml"
    workflow.write_text("before\nneedle\n", encoding="utf-8")

    count = local_checkout.ripgrep_local_result(
        checkout_dir=str(tmp_path),
        pattern="needle",
        case_insensitive=False,
        path=".github/workflows/ci.yml",
        glob=None,
        filename_filter=re.compile(r".*\.yml$"),
        output_mode="count",
        context_lines=0,
        reverse=False,
        max_output_chars=1000,
        grep_max_files=10,
        truncation_hint="truncated",
    )
    content = local_checkout.ripgrep_local_result(
        checkout_dir=str(tmp_path),
        pattern="needle",
        case_insensitive=False,
        path=".github/workflows/ci.yml",
        glob=None,
        filename_filter=re.compile(r".*\.yml$"),
        output_mode="content",
        context_lines=0,
        reverse=False,
        max_output_chars=1000,
        grep_max_files=10,
        truncation_hint="truncated",
    )

    assert count["text"] == "/.github/workflows/ci.yml:1"
    assert "/.github/workflows/ci.yml" in content["text"]


def test_apply_sparse_patterns_surfaces_git_failures(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    marker = checkout / ".git" / "smith-sparse-patterns"
    marker.parent.mkdir(parents=True)
    marker.write_text("old", encoding="utf-8")

    def _failing_git(args: list[str]) -> None:
        raise RuntimeError("git failed")

    with pytest.raises(SmithError, match="Could not apply sparse-checkout"):
        local_checkout.apply_sparse_patterns(_failing_git, str(checkout), ["/*"])
