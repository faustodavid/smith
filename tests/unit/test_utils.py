from __future__ import annotations

from datetime import UTC, datetime

import pytest

from smith.utils import (
    compile_search_pattern,
    match_all_pattern,
    normalize_path,
    parse_iso_datetime,
    slice_lines,
)


@pytest.mark.parametrize(
    ("raw_path", "expected"),
    [
        (None, "/"),
        ("", "/"),
        ("/already/prefixed", "/already/prefixed"),
        ("no/prefix", "/no/prefix"),
    ],
)
def test_normalize_path_handles_expected_inputs(raw_path: str | None, expected: str) -> None:
    assert normalize_path(raw_path) == expected


def test_parse_iso_datetime_returns_none_for_none_input() -> None:
    assert parse_iso_datetime(None) is None


def test_parse_iso_datetime_keeps_timezone_aware_datetime() -> None:
    value = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)

    assert parse_iso_datetime(value) is value


def test_parse_iso_datetime_adds_utc_to_naive_datetime() -> None:
    value = datetime(2024, 1, 2, 3, 4, 5)

    parsed = parse_iso_datetime(value)

    assert parsed == datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_parse_iso_datetime_parses_iso_string() -> None:
    parsed = parse_iso_datetime("2024-01-02T03:04:05+00:00")

    assert parsed == datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_parse_iso_datetime_parses_iso_string_with_z_suffix() -> None:
    parsed = parse_iso_datetime("2024-01-02T03:04:05Z")

    assert parsed == datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_parse_iso_datetime_returns_none_for_invalid_string() -> None:
    assert parse_iso_datetime("not-a-datetime") is None


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        (".*", True),
        ("^.*$", True),
        ("^.*", True),
        (".*$", True),
        ("foo", False),
    ],
)
def test_match_all_pattern_identifies_only_match_all_variants(pattern: str, expected: bool) -> None:
    assert match_all_pattern(pattern) is expected


def test_slice_lines_without_range_returns_original_list() -> None:
    lines = ["a", "b", "c", "d"]

    sliced = slice_lines(lines, from_line=None, to_line=None)

    assert sliced is lines


def test_slice_lines_from_line_only() -> None:
    lines = ["a", "b", "c", "d"]

    assert slice_lines(lines, from_line=2, to_line=None) == ["b", "c", "d"]


def test_slice_lines_to_line_only() -> None:
    lines = ["a", "b", "c", "d"]

    assert slice_lines(lines, from_line=None, to_line=2) == ["a", "b"]


def test_slice_lines_with_both_bounds() -> None:
    lines = ["a", "b", "c", "d"]

    assert slice_lines(lines, from_line=2, to_line=3) == ["b", "c"]


@pytest.mark.parametrize(
    ("from_line", "to_line", "expected"),
    [
        (10, None, []),
        (3, 2, []),
        (0, 0, ["a", "b", "c", "d"]),
    ],
)
def test_slice_lines_edge_cases(
    from_line: int | None,
    to_line: int | None,
    expected: list[str],
) -> None:
    lines = ["a", "b", "c", "d"]

    assert slice_lines(lines, from_line=from_line, to_line=to_line) == expected


def test_compile_search_pattern_returns_compiled_regex_for_valid_pattern() -> None:
    compiled, error = compile_search_pattern(r"foo\d+", case_insensitive=False)

    assert error is None
    assert compiled is not None
    assert compiled.search("foo123") is not None


def test_compile_search_pattern_returns_error_for_invalid_pattern() -> None:
    compiled, error = compile_search_pattern("(", case_insensitive=False)

    assert compiled is None
    assert error is not None
    assert "Invalid regex pattern" in error


def test_compile_search_pattern_honors_case_insensitive_flag() -> None:
    compiled, error = compile_search_pattern("hello", case_insensitive=True)

    assert error is None
    assert compiled is not None
    assert compiled.search("HELLO") is not None
