from __future__ import annotations

import re
from typing import Any, Literal

from smith.formatting import format_grep_matches, truncate_output
from smith.utils import compile_search_pattern, match_all_pattern, slice_lines


def paginate_results(results: list[Any], *, skip: int, take: int) -> list[Any]:
    start = max(0, skip)
    return results[start : start + max(1, take)]


def grep_compile_error_result(
    error_text: str,
    *,
    matched_key: str = "files_matched",
) -> dict[str, Any]:
    return {
        "text": error_text,
        matched_key: 0,
        "warnings": [],
        "partial": False,
    }


def grep_too_many_files_result(
    candidate_count: int,
    limit: int,
    *,
    matched_key: str = "files_matched",
) -> dict[str, Any]:
    return {
        "text": (
            f"Search scope contains {candidate_count} candidate files which exceeds the safety limit ({limit}).\n"
            "Narrow your search:\n"
            "  --path <dir>       restrict to a subdirectory\n"
            '  --glob "*.py"      restrict to a file type\n'
            'Or use `smith code search "<query>"` first to locate the right path, then grep with --path.'
        ),
        matched_key: 0,
        "warnings": [
            (
                f"candidate file count {candidate_count} exceeds SMITH_GREP_MAX_FILES={limit}; "
                "narrow with --path/--glob or start with `smith code search`."
            )
        ],
        "partial": True,
    }


def grep_match_lines(
    *,
    lines: list[str],
    search_pattern: re.Pattern[str],
    file_label: str,
    output_mode: str,
    context_lines: int,
) -> tuple[list[str], int]:
    match_line_nums = {idx for idx, line in enumerate(lines) if search_pattern.search(line)}
    if not match_line_nums:
        return [], 0

    if output_mode == "files_with_matches" or output_mode == "logs_with_matches":
        return [file_label], 1
    if output_mode == "count":
        return [f"{file_label}:{len(match_line_nums)}"], 1
    return (
        format_grep_matches(
            file_label,
            lines,
            match_line_nums,
            context_lines,
            include_line_numbers=True,
        ),
        1,
    )


def build_grep_result(
    *,
    output_lines: list[str],
    matched_count: int,
    warnings: list[str],
    max_output_chars: int,
    truncation_hint: str,
    matched_key: str = "files_matched",
) -> dict[str, Any]:
    text = "\n".join(output_lines)
    text = truncate_output(text, max_output_chars, truncation_hint)
    return {
        "text": text,
        matched_key: matched_count,
        "warnings": warnings,
        "partial": bool(warnings),
    }


def grep_build_logs_core(
    *,
    log_ids: list[int],
    get_content: Any,
    pattern: str | None,
    output_mode: Literal["content", "logs_with_matches", "count"],
    case_insensitive: bool,
    context_lines: int | None,
    from_line: int | None,
    to_line: int | None,
    max_output_chars: int,
) -> dict[str, Any]:
    regex_pattern = pattern or ".*"
    is_match_all = match_all_pattern(regex_pattern)

    if not log_ids and is_match_all:
        return grep_compile_error_result(
            "Error: Specify a log_id to read full content, or provide a pattern to search across all logs.",
            matched_key="logs_matched",
        )

    search_pattern, compile_error = compile_search_pattern(
        regex_pattern, case_insensitive=case_insensitive
    )
    if compile_error or search_pattern is None:
        return grep_compile_error_result(compile_error or "Invalid pattern", matched_key="logs_matched")

    single_log = len(log_ids) == 1
    if single_log and is_match_all:
        log_id = log_ids[0]
        content = get_content(log_id)
        lines = slice_lines(content.splitlines(), from_line=from_line, to_line=to_line)

        if output_mode == "logs_with_matches":
            return {"text": f"Log {log_id}", "logs_matched": 1, "warnings": [], "partial": False}
        if output_mode == "count":
            return {"text": f"Log {log_id}:{len(lines)}", "logs_matched": 1, "warnings": [], "partial": False}

        start = from_line or 1
        text = "\n".join(f"{start + idx}:{line}" for idx, line in enumerate(lines))
        text = truncate_output(text, max_output_chars, "Use from_line/to_line to read specific ranges.")
        return {"text": text, "logs_matched": 1, "warnings": [], "partial": False}

    output_lines: list[str] = []
    warnings: list[str] = []
    logs_matched = 0

    for current_log_id in log_ids:
        try:
            content = get_content(current_log_id)
        except Exception as exc:
            warnings.append(f"failed to read Log {current_log_id}: {exc}")
            continue

        lines = slice_lines(content.splitlines(), from_line=from_line, to_line=to_line)
        matched_lines, count = grep_match_lines(
            lines=lines,
            search_pattern=search_pattern,
            file_label=f"Log {current_log_id}",
            output_mode=output_mode,
            context_lines=context_lines or 0,
        )
        if count:
            logs_matched += count
            output_lines.extend(matched_lines)

    return build_grep_result(
        output_lines=output_lines,
        matched_count=logs_matched,
        warnings=warnings,
        max_output_chars=max_output_chars,
        truncation_hint="Use from_line/to_line to read specific ranges, or narrow with pattern/log-id.",
        matched_key="logs_matched",
    )
