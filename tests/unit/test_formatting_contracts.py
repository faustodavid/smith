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


def test_format_grep_matches_applies_line_offset_for_absolute_numbers() -> None:
    rendered = format_grep_matches(
        "/repo/log.txt",
        ["first", "match", "third"],
        {1},
        context_lines=0,
        include_line_numbers=True,
        line_offset=99,
    )

    assert rendered == [
        "/repo/log.txt",
        "101:match",
    ]


def test_format_grep_matches_reverse_flips_block_order_preserving_ascending_within_block() -> None:
    rendered = format_grep_matches(
        "/repo/file.py",
        ["zero", "one", "two", "three", "four", "five", "six"],
        {1, 5},
        context_lines=1,
        include_line_numbers=True,
        reverse=True,
    )

    assert rendered == [
        "/repo/file.py",
        "5-four",
        "6:five",
        "7-six",
        "--",
        "1-zero",
        "2:one",
        "3-two",
    ]


def test_format_grep_matches_reverse_combined_with_line_offset() -> None:
    rendered = format_grep_matches(
        "/repo/log.txt",
        ["nine-hundred", "nine-oh-one", "nine-oh-two"],
        {0, 2},
        context_lines=0,
        include_line_numbers=True,
        line_offset=899,
        reverse=True,
    )

    assert rendered == [
        "/repo/log.txt",
        "902:nine-oh-two",
        "--",
        "900:nine-hundred",
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


def test_render_text_renders_prs_pipeline_and_story_views() -> None:
    pr_list = render_text(
        "prs.list",
        {
            "results": [{"project_name": "proj", "repository_name": "repo", "pr_id": 17, "status": "active", "title": "Fix"}],
            "returned_count": 1,
            "has_more": False,
        },
    )
    pr_search = render_text(
        "prs.search",
        {
            "results": [{"project_name": "proj", "repository_name": "repo", "pr_id": 18, "status": "completed", "title": "Search"}],
            "returned_count": 1,
            "has_more": False,
        },
    )
    pr_get = render_text(
        "prs.get",
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
        "prs.threads",
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
        "pipelines.logs.list",
        {
            "metadata": {"build_id": 101, "status": "completed", "result": "failed", "definition_name": "CI"},
            "logs": [{"id": 1, "line_count": 50, "stage_name": "Build", "job_name": "linux", "step_name": "pytest"}],
        },
    )
    work_get = render_text(
        "stories.get",
        {"id": 9, "fields": {"System.WorkItemType": "Bug", "System.State": "Active", "System.Title": "Fix login"}},
    )
    work_search = render_text(
        "stories.search",
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
    assert pr_search == (
        "project | repo | pr_id | status | title\n"
        "proj | repo | 18 | completed | Search\n"
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
    assert work_search == "id | type | state | title\n9 | Bug | Active | Fix login\nreturned_count: 1\nhas_more: False"


def test_render_text_renders_pr_get_diffs_when_present() -> None:
    rendered = render_text(
        "prs.get",
        {
            "pull_request": {
                "pullRequestId": 17,
                "title": "Fix it",
                "status": "active",
                "createdBy": {"displayName": "alice"},
                "sourceRefName": "refs/heads/feature",
                "targetRefName": "refs/heads/main",
            },
            "threads": [],
            "diffs": {
                "src/app.py": "@@ -1 +1 @@\n-old\n+new",
                "src/util.py": "@@ -2 +2 @@\n-before\n+after",
            },
        },
    )

    assert rendered == (
        "id: 17\n"
        "title: Fix it\n"
        "status: active\n"
        "creator: alice\n"
        "source_branch: feature\n"
        "target_branch: main\n"
        "comments_threads: 0\n"
        "diffs:\n"
        "diff: src/app.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "diff: src/util.py\n"
        "@@ -2 +2 @@\n"
        "-before\n"
        "+after"
    )


def test_render_text_code_search_shows_total_and_displayed_counts() -> None:
    rendered = render_text(
        "code.search",
        {
            "matchesCount": 5,
            "results": ["repo:/src/app.py", "repo:/src/lib.py"],
        },
    )

    assert rendered == (
        "matches: 5 (showing 2)\n"
        "repo:/src/app.py\n"
        "repo:/src/lib.py"
    )


def test_render_text_renders_youtrack_story_views() -> None:
    work_get = render_text(
        "stories.get",
        {
            "provider": "youtrack",
            "id": "RAD-1055",
            "title": "Investigate patching flow",
            "url": "https://youtrack.example.test/issue/RAD-1055",
            "metadata": {
                "Project": "Release and Deployments (RAD)",
                "Type": "Task",
                "State": "In Progress",
                "Priority": "Normal",
                "Reporter": "Fausto Suarez Rosario (fausto)",
                "Created": "2026-04-12T10:00:00Z",
                "Impact": "4",
            },
            "description": "Line one\n![](image1.png)",
            "attachments": [
                {
                    "name": "image1.png",
                    "mimeType": "image/png",
                    "size": 123,
                    "url": "https://youtrack.example.test/api/files/1",
                }
            ],
            "links": [
                {
                    "type": "Relates",
                    "issues": [{"id": "RAD-1004", "summary": "Implement endpoint"}],
                }
            ],
            "comments": [
                {
                    "author_display": "Fausto Suarez Rosario (fausto)",
                    "created": "2026-04-12T11:00:00Z",
                    "text": "Looks good",
                    "reactions": [{"reaction": "thumbs-up", "author_display": "alice"}],
                    "attachments": [],
                }
            ],
            "timeline": [
                {
                    "timestamp": "2026-04-12T10:00:00Z",
                    "author_display": "Fausto Suarez Rosario (fausto)",
                    "action": "created issue",
                }
            ],
        },
    )
    work_search = render_text(
        "stories.search",
        {
            "provider": "youtrack",
            "results": [
                {
                    "id": "RAD-1055",
                    "project": "RAD",
                    "type": "Task",
                    "state": "In Progress",
                    "title": "Investigate patching flow",
                }
            ],
            "returned_count": 1,
            "has_more": False,
        },
    )

    assert "--- description ---" in work_get
    assert "--- attachments ---" in work_get
    assert "--- related items ---" in work_get
    assert "--- comments (1) ---" in work_get
    assert "--- timeline (1) ---" in work_get
    assert "Impact: 4" in work_get
    assert work_search == (
        "id | project | type | state | title\n"
        "RAD-1055 | RAD | Task | In Progress | Investigate patching flow\n"
        "returned_count: 1\n"
        "has_more: False"
    )


def test_render_text_code_search_shows_plus_for_lower_bound_totals() -> None:
    rendered = render_text(
        "code.search",
        {
            "matchesCount": 200,
            "matchesCountLowerBound": True,
            "results": ["repo:/src/app.py", "repo:/src/lib.py"],
        },
    )

    assert rendered == (
        "matches: 200+ (showing 2)\n"
        "repo:/src/app.py\n"
        "repo:/src/lib.py"
    )


def test_render_text_grouped_remote_output_preserves_order_warnings_and_errors() -> None:
    payload = {
        "remotes": {
            "github": {
                "ok": True,
                "data": {"matchesCount": 2, "results": ["repo:/src/app.py"]},
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
        "[github] matches: 2 (showing 1)\n"
        "repo:/src/app.py\n"
        "\n"
        "[azdo]\n"
        "error: rate limited"
    )


def test_render_text_grouped_code_search_hides_lower_bound_warning_and_shows_plus() -> None:
    payload = {
        "remotes": {
            "gitlab": {
                "ok": True,
                "data": {
                    "matchesCount": 200,
                    "matchesCountLowerBound": True,
                    "results": ["repo:/src/app.py"],
                },
                "warnings": [
                    "GitLab search did not provide an exact total; `matchesCount` is a lower bound. "
                    "Narrow with `--repo group/project` for exact counts."
                ],
                "partial": True,
                "error": None,
            }
        },
        "summary": {"queried": ["gitlab"]},
    }

    assert render_text("code.search", payload) == (
        "matches: 200+ (showing 1)\n"
        "repo:/src/app.py"
    )


def test_render_text_flattens_single_remote_and_omits_duplicate_grep_warnings() -> None:
    payload = {
        "remotes": {
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

    assert render_text("pipelines.logs.grep", payload) == "line one\nwarning: inner warning"


def test_render_text_returns_remote_error_for_single_remote_failures() -> None:
    payload = {
        "remotes": {
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


def test_render_text_discovery_payload_uses_results_window() -> None:
    rendered = render_text(
        "groups",
        {
            "results": [{"name": "platform/api"}, {"name": "platform/web"}],
            "returned_count": 2,
            "has_more": True,
        },
    )

    assert rendered == "platform/api\nplatform/web"


def test_render_text_grouped_discovery_output_surfaces_warning_and_partial() -> None:
    payload = {
        "remotes": {
            "gitlab-infra": {
                "ok": True,
                "data": {"results": [{"name": "platform/api"}]},
                "warnings": ["showing 1 matching groups; use --skip/--take to see more."],
                "partial": True,
                "error": None,
            }
        },
        "summary": {"queried": ["gitlab-infra"]},
    }

    assert render_text("groups", payload) == (
        "platform/api\n"
        "warning: showing 1 matching groups; use --skip/--take to see more."
    )


def test_render_text_falls_back_to_json_for_unknown_commands() -> None:
    assert render_text("custom.command", {"alpha": 1, "beta": 2}) == '{\n  "alpha": 1,\n  "beta": 2\n}'


def test_render_text_config_list_renders_tabular_toon_with_normalized_fields() -> None:
    rendered = render_text(
        "config.list",
        {
            "remotes": [
                {
                    "name": "github",
                    "provider": "github",
                    "enabled": True,
                    "org": "octo-org",
                    "host": "github.com",
                },
                {
                    "name": "gitlab-infra",
                    "provider": "gitlab",
                    "enabled": False,
                    "host": "gitlab-infra.example.com",
                },
            ]
        },
    )

    assert rendered == (
        "remotes[2]{name,provider,enabled,org,host}:\n"
        "  github,github,true,octo-org,github.com\n"
        "  gitlab-infra,gitlab,false,null,gitlab-infra.example.com"
    )


def test_render_text_config_list_empty_state_uses_toon_zero_length_array() -> None:
    assert render_text("config.list", {"remotes": []}) == "remotes[0]:"


def test_render_text_config_show_renders_key_value_lines_and_omits_missing_fields() -> None:
    rendered = render_text(
        "config.show",
        {
            "name": "gitlab-infra",
            "provider": "gitlab",
            "enabled": False,
            "host": "gitlab-infra.example.com",
            "token_env": "GITLAB_INFRA_TOKEN",
            "api_url": "https://gitlab-infra.example.com/api/v4",
        },
    )

    assert rendered == (
        "name: gitlab-infra\n"
        "provider: gitlab\n"
        "enabled: false\n"
        "host: gitlab-infra.example.com\n"
        "token_env: GITLAB_INFRA_TOKEN\n"
        "api_url: https://gitlab-infra.example.com/api/v4"
    )


def test_render_text_pipelines_list_emits_token_optimized_tree() -> None:
    rendered = render_text(
        "pipelines.list",
        {
            "pipelines": [
                {
                    "id": 998877,
                    "project_id": 882,
                    "project": "acme/api",
                    "status": "running",
                    "ref": "feat/api",
                    "jobs": [
                        {
                            "id": 10,
                            "name": "compile",
                            "stage": "build",
                            "status": "success",
                            "duration_s": 200,
                        },
                        {
                            "id": 21,
                            "name": "unit",
                            "stage": "test",
                            "status": "success",
                            "duration_s": 60,
                            "matrix": [1, 2],
                            "needs": ["compile"],
                        },
                        {
                            "id": 22,
                            "name": "unit",
                            "stage": "test",
                            "status": "success",
                            "duration_s": 65,
                            "matrix": [2, 2],
                            "needs": ["compile"],
                        },
                        {
                            "id": 23,
                            "name": "lint",
                            "stage": "test",
                            "status": "failed",
                            "duration_s": 40,
                            "allow_failure": True,
                            "needs": ["compile"],
                        },
                        {
                            "id": 30,
                            "name": "staging_up",
                            "stage": "deploy",
                            "status": "success",
                            "duration_s": 300,
                            "environment": "staging",
                            "needs": ["unit"],
                        },
                        {
                            "id": 31,
                            "name": "prod_up",
                            "stage": "deploy",
                            "status": "manual",
                            "duration_s": 0,
                            "manual": True,
                            "environment": "prod",
                            "needs": ["staging_up"],
                            "downstream": {
                                "project": "ops/infra",
                                "pipeline_id": 1122,
                                "status": "created",
                            },
                        },
                    ],
                },
            ],
            "returned_count": 1,
            "total_count": 1,
            "warnings": [],
            "partial": False,
        },
    )

    assert rendered == (
        "@p:998877|prj:882|ref:feat/api|st:run\n"
        "#build\n"
        "*j10:compile|ok|200s\n"
        "#test\n"
        "*j21:unit[1/2]|ok|60s <j10\n"
        "*j22:unit[2/2]|ok|65s <j10\n"
        "*j23:lint|err|40s! <j10\n"
        "#deploy\n"
        "*j30:staging_up|ok|300s^staging <unit\n"
        "*j31:prod_up|man|0s?^prod <j30 >> ops/infra:1122[cre]\n"
        "returned_count: 1\n"
        "total_count: 1"
    )


def test_render_text_pipelines_list_header_falls_back_to_project_path() -> None:
    rendered = render_text(
        "pipelines.list",
        {
            "pipelines": [
                {
                    "id": 55,
                    "project": "repo-a",
                    "status": "success",
                    "ref": "main",
                },
            ],
            "returned_count": 1,
            "total_count": 1,
            "warnings": [],
            "partial": False,
        },
    )

    assert rendered.splitlines()[0] == "@p:55|prj:repo-a|ref:main|st:ok"


def test_render_text_pipelines_list_header_includes_pipeline_name() -> None:
    rendered = render_text(
        "pipelines.list",
        {
            "pipelines": [
                {
                    "id": 55,
                    "project": "repo-a",
                    "status": "success",
                    "ref": "main",
                    "name": "Main release pipeline",
                },
            ],
            "returned_count": 1,
            "total_count": 1,
        },
    )

    assert rendered.splitlines()[0] == "@p:55|prj:repo-a|ref:main|st:ok|nm:Main release pipeline"


def test_render_text_pipelines_list_header_includes_via_stage_and_job() -> None:
    rendered = render_text(
        "pipelines.list",
        {
            "pipelines": [
                {
                    "id": 55,
                    "project": "repo-a",
                    "status": "failed",
                    "ref": "main",
                    "name": "Main release pipeline",
                    "trigger_stage": "deploy",
                    "trigger_job": "release-pipeline",
                },
            ],
            "returned_count": 1,
            "total_count": 1,
        },
    )

    assert (
        rendered.splitlines()[0]
        == "@p:55|prj:repo-a|ref:main|st:err|nm:Main release pipeline|via:deploy/release-pipeline"
    )


def test_render_text_pipelines_list_renders_stage_less_jobs_without_stage_header() -> None:
    rendered = render_text(
        "pipelines.list",
        {
            "pipelines": [
                {
                    "id": 900,
                    "project_id": 7,
                    "status": "running",
                    "ref": "main",
                    "jobs": [
                        {"id": 1, "name": "build", "status": "success", "duration_s": 30},
                        {"id": 2, "name": "test", "status": "running", "duration_s": None},
                    ],
                },
            ],
            "returned_count": 1,
            "total_count": 1,
        },
    )

    assert rendered == (
        "@p:900|prj:7|ref:main|st:run\n"
        "*j1:build|ok|30s\n"
        "*j2:test|run|-\n"
        "returned_count: 1\n"
        "total_count: 1"
    )
