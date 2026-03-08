from __future__ import annotations

import re

from smith.formatting import format_grep_matches, glob_to_regex, normalize_branch_name, render_text, truncate_output


def test_normalize_branch_name_handles_head_pull_and_passthrough_values() -> None:
    assert normalize_branch_name(" refs/heads/main ") == "main"
    assert normalize_branch_name("refs/pull/42/merge") == "pr/42"
    assert normalize_branch_name("refs/pull/42/head") == "pr/42/head"
    assert normalize_branch_name("feature/branch") == "feature/branch"
    assert normalize_branch_name("   ") is None


def test_glob_to_regex_supports_recursive_globs_and_groups() -> None:
    pattern = re.compile(glob_to_regex("src/**/{main,test}.py"))

    assert pattern.search("src/main.py") is not None
    assert pattern.search("src/smith/test.py") is not None
    assert pattern.search("src/smith/deep/main.py") is not None
    assert pattern.search("src/smith/deep/main.ts") is None


def test_format_grep_matches_includes_context_and_gap_markers() -> None:
    rendered = format_grep_matches(
        "/repo/file.py",
        ["zero", "one", "two", "three", "four", "five"],
        {1, 4},
        context_lines=1,
        include_line_numbers=True,
    )

    assert rendered == [
        "/repo/file.py",
        "1-zero",
        "2:one",
        "3-two",
        "4-three",
        "5:four",
        "6-five",
    ]


def test_truncate_output_reports_character_and_line_counts() -> None:
    text = "alpha\nbeta\ngamma\ndelta"

    assert truncate_output(text, 0, "unused") == text
    truncated = truncate_output(text, 8, "narrow it")
    assert truncated == (
        "alpha\nbe\n"
        "... (truncated: showing 8 of 22 characters, 2 of 4 lines)\n"
        "narrow it"
    )


def test_render_text_renders_pr_ci_and_story_views() -> None:
    pr_list = render_text(
        "pr.list",
        {
            "results": [{"project_name": "proj", "repository_name": "repo", "pr_id": 17, "status": "active", "title": "Fix"}],
            "returned_count": 1,
            "has_more": False,
        },
    )
    pr_get = render_text(
        "pr.get",
        {
            "pull_request": {
                "pullRequestId": 17,
                "title": "Fix it",
                "status": "active",
                "createdBy": {"displayName": "alice"},
                "sourceRefName": "refs/heads/feature",
                "targetRefName": "refs/heads/main",
            },
            "threads": [{}, {}],
        },
    )
    pr_threads = render_text(
        "pr.threads",
        {
            "pull_request_id": 17,
            "returned_count": 1,
            "total_comments": 2,
            "threads": [
                {
                    "id": 5,
                    "status": "active",
                    "comment_count": 2,
                    "file_path": "/src/app.py",
                    "line_start": 18,
                    "comments": [{"author": "alice", "content": "Looks good"}],
                }
            ],
        },
    )
    ci_logs = render_text(
        "ci.logs.list",
        {
            "metadata": {"build_id": 101, "status": "completed", "result": "failed", "definition_name": "CI"},
            "logs": [{"id": 1, "line_count": 50, "stage_name": "Build", "job_name": "linux", "step_name": "pytest"}],
        },
    )
    work_get = render_text(
        "stories.get",
        {"id": 9, "fields": {"System.WorkItemType": "Bug", "System.State": "Active", "System.Title": "Fix login"}},
    )
    work_query = render_text(
        "stories.query",
        {
            "results": [{"id": 9, "type": "Bug", "state": "Active", "title": "Fix login"}],
            "returned_count": 1,
            "has_more": False,
        },
    )

    assert pr_list == (
        "project | repo | pr_id | status | title\n"
        "proj | repo | 17 | active | Fix\n"
        "returned_count: 1\n"
        "has_more: False"
    )
    assert pr_get == (
        "id: 17\n"
        "title: Fix it\n"
        "status: active\n"
        "creator: alice\n"
        "source_branch: feature\n"
        "target_branch: main\n"
        "comments_threads: 2"
    )
    assert pr_threads == (
        "pull_request_id: 17\n"
        "returned_count: 1\n"
        "total_comments: 2\n"
        "thread 5 status=active comments=2 file=/src/app.py:18\n"
        "  - alice: Looks good"
    )
    assert ci_logs == (
        "build_id: 101\n"
        "status: completed\n"
        "result: failed\n"
        "definition: CI\n"
        "logs:\n"
        "1 | 50 | Build | linux | pytest"
    )
    assert work_get == "id: 9\ntype: Bug\nstate: Active\ntitle: Fix login"
    assert work_query == "id | type | state | title\n9 | Bug | Active | Fix login\nreturned_count: 1\nhas_more: False"


def test_render_text_grouped_provider_output_preserves_order_warnings_and_errors() -> None:
    payload = {
        "providers": {
            "github": {
                "ok": True,
                "data": {"matchesCount": 2, "results": ["octo/repo:/src/app.py"]},
                "warnings": [],
                "partial": False,
                "error": None,
            },
            "azdo": {
                "ok": False,
                "data": None,
                "warnings": [],
                "partial": False,
                "error": {"message": "rate limited"},
            },
        },
        "summary": {"queried": ["github", "azdo"]},
    }

    assert render_text("code.search", payload) == (
        "[github] matches: 2\n"
        "octo/repo:/src/app.py\n"
        "\n"
        "[azdo]\n"
        "error: rate limited"
    )


def test_render_text_flattens_single_provider_and_omits_duplicate_grep_warnings() -> None:
    payload = {
        "providers": {
            "github": {
                "ok": True,
                "data": {"text": "line one", "warnings": ["inner warning"]},
                "warnings": ["outer warning"],
                "partial": True,
                "error": None,
            }
        },
        "summary": {"queried": ["github"]},
    }

    assert render_text("ci.logs.grep", payload) == "line one\nwarning: inner warning\npartial: true"


def test_render_text_returns_provider_error_for_single_provider_failures() -> None:
    payload = {
        "providers": {
            "azdo": {
                "ok": False,
                "data": None,
                "warnings": [],
                "partial": False,
                "error": {"message": "missing org"},
            }
        },
        "summary": {"queried": ["azdo"]},
    }

    assert render_text("repos", payload) == "error: missing org"


def test_render_text_repos_uses_project_column_for_cross_project_results() -> None:
    rendered = render_text(
        "repos",
        [
            {"projectName": "proj-a", "name": "repo-a"},
            {"projectName": "proj-b", "name": "repo-b"},
        ],
    )

    assert rendered == "project | repo\nproj-a | repo-a\nproj-b | repo-b"


def test_render_text_falls_back_to_json_for_unknown_commands() -> None:
    assert render_text("custom.command", {"alpha": 1, "beta": 2}) == '{\n  "alpha": 1,\n  "beta": 2\n}'
