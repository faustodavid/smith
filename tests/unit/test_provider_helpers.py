from __future__ import annotations

import re
from unittest.mock import Mock

from smith.providers.helpers import (
    build_grep_result,
    grep_build_logs_core,
    grep_compile_error_result,
    grep_match_lines,
    grep_too_many_files_result,
    paginate_results,
)


def test_paginate_results_with_skip_zero_take_five() -> None:
    values = list(range(10))

    assert paginate_results(values, skip=0, take=5) == [0, 1, 2, 3, 4]


def test_paginate_results_with_skip_three_take_five() -> None:
    values = list(range(10))

    assert paginate_results(values, skip=3, take=5) == [3, 4, 5, 6, 7]


def test_paginate_results_with_skip_beyond_list_length() -> None:
    values = list(range(10))

    assert paginate_results(values, skip=20, take=5) == []


def test_paginate_results_with_negative_skip_starts_from_zero() -> None:
    values = list(range(10))

    assert paginate_results(values, skip=-4, take=5) == [0, 1, 2, 3, 4]


def test_grep_compile_error_result_uses_default_matched_key() -> None:
    result = grep_compile_error_result("Error text")

    assert result["text"] == "Error text"
    assert result["files_matched"] == 0
    assert result["warnings"] == []
    assert result["partial"] is False


def test_grep_compile_error_result_uses_custom_matched_key() -> None:
    result = grep_compile_error_result("Error text", matched_key="logs_matched")

    assert result["text"] == "Error text"
    assert result["logs_matched"] == 0
    assert result["warnings"] == []
    assert result["partial"] is False


def test_grep_too_many_files_result_returns_partial_guidance() -> None:
    result = grep_too_many_files_result(7000, 5000)

    assert result["files_matched"] == 0
    assert result["partial"] is True
    assert result["warnings"] == [
        "candidate file count 7000 exceeds SMITH_GREP_MAX_FILES=5000; narrow with --path/--glob or start with `smith code search`."
    ]
    assert "Search scope contains 7000 candidate files" in result["text"]
    assert '--glob "*.py"' in result["text"]


def test_grep_match_lines_returns_content_mode_output_for_matches() -> None:
    lines = ["alpha", "foo", "beta foo"]
    pattern = re.compile("foo")

    output, matched = grep_match_lines(
        lines=lines,
        search_pattern=pattern,
        file_label="file.txt",
        output_mode="content",
        context_lines=0,
    )

    assert matched == 1
    assert output[0] == "file.txt"
    assert "2:foo" in output
    assert "3:beta foo" in output


def test_grep_match_lines_returns_files_with_matches_mode_output() -> None:
    lines = ["alpha", "foo", "beta"]
    pattern = re.compile("foo")

    output, matched = grep_match_lines(
        lines=lines,
        search_pattern=pattern,
        file_label="file.txt",
        output_mode="files_with_matches",
        context_lines=0,
    )

    assert output == ["file.txt"]
    assert matched == 1


def test_grep_match_lines_returns_count_mode_output() -> None:
    lines = ["foo", "bar", "foo"]
    pattern = re.compile("foo")

    output, matched = grep_match_lines(
        lines=lines,
        search_pattern=pattern,
        file_label="file.txt",
        output_mode="count",
        context_lines=0,
    )

    assert output == ["file.txt:2"]
    assert matched == 1


def test_grep_match_lines_returns_empty_when_no_matches() -> None:
    lines = ["alpha", "beta", "gamma"]
    pattern = re.compile("foo")

    output, matched = grep_match_lines(
        lines=lines,
        search_pattern=pattern,
        file_label="file.txt",
        output_mode="content",
        context_lines=0,
    )

    assert output == []
    assert matched == 0


def test_build_grep_result_returns_text_and_partial_flag_with_warnings() -> None:
    result = build_grep_result(
        output_lines=["line-1", "line-2"],
        matched_count=2,
        warnings=["warning text"],
        max_output_chars=100,
        truncation_hint="hint text",
    )

    assert result["text"] == "line-1\nline-2"
    assert result["files_matched"] == 2
    assert result["warnings"] == ["warning text"]
    assert result["partial"] is True


def test_grep_build_logs_core_match_all_with_single_log_returns_numbered_content() -> None:
    get_content = Mock(return_value="line one\nline two")

    result = grep_build_logs_core(
        log_ids=[42],
        get_content=get_content,
        pattern=".*",
        output_mode="content",
        case_insensitive=False,
        context_lines=None,
        from_line=None,
        to_line=None,
        max_output_chars=1000,
    )

    assert result == {
        "text": "1:line one\n2:line two",
        "logs_matched": 1,
        "warnings": [],
        "partial": False,
    }
    get_content.assert_called_once_with(42)


def test_grep_build_logs_core_search_pattern_across_multiple_logs() -> None:
    log_contents = {
        1: "alpha\nerror happened",
        2: "beta\nERROR happened",
        3: "beta\ngamma",
    }
    get_content = Mock(side_effect=lambda log_id: log_contents[log_id])

    result = grep_build_logs_core(
        log_ids=[1, 2, 3],
        get_content=get_content,
        pattern="error",
        output_mode="logs_with_matches",
        case_insensitive=True,
        context_lines=0,
        from_line=None,
        to_line=None,
        max_output_chars=1000,
    )

    assert result["text"] == "Log 1\nLog 2"
    assert result["logs_matched"] == 2
    assert result["warnings"] == []
    assert result["partial"] is False


def test_grep_build_logs_core_returns_compile_error_result_for_invalid_pattern() -> None:
    get_content = Mock(return_value="alpha")

    result = grep_build_logs_core(
        log_ids=[1],
        get_content=get_content,
        pattern="[",
        output_mode="content",
        case_insensitive=False,
        context_lines=0,
        from_line=None,
        to_line=None,
        max_output_chars=1000,
    )

    assert result["logs_matched"] == 0
    assert result["warnings"] == []
    assert result["partial"] is False
    assert result["text"].startswith("Error: Invalid regex pattern")


def test_grep_build_logs_core_requires_log_id_for_match_all_without_logs() -> None:
    get_content = Mock()

    result = grep_build_logs_core(
        log_ids=[],
        get_content=get_content,
        pattern=".*",
        output_mode="content",
        case_insensitive=False,
        context_lines=0,
        from_line=None,
        to_line=None,
        max_output_chars=1000,
    )

    assert result == {
        "text": "Error: Specify a log_id to read full content, or provide a pattern to search across all logs.",
        "logs_matched": 0,
        "warnings": [],
        "partial": False,
    }
    get_content.assert_not_called()
