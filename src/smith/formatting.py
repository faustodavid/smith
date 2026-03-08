#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from typing import Any


def make_envelope(
    *,
    ok: bool,
    command: str,
    data: Any = None,
    meta: dict[str, Any] | None = None,
    error: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "command": command,
        "meta": meta or {},
        "data": data,
        "error": error,
    }


def dumps_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True)


def normalize_branch_name(ref: str | None) -> str | None:
    if ref is None:
        return None
    normalized = ref.strip()
    if not normalized:
        return None
    if normalized.startswith("refs/heads/"):
        return normalized.replace("refs/heads/", "", 1)
    if normalized.startswith("refs/pull/"):
        parts = normalized.split("/")
        if len(parts) >= 4:
            if parts[3] == "merge":
                return f"pr/{parts[2]}"
            return f"pr/{parts[2]}/{parts[3]}"
    return normalized


def glob_to_regex(glob_pattern: str) -> str:
    result: list[str] = []
    i = 0
    while i < len(glob_pattern):
        c = glob_pattern[i]
        if c == "*":
            if i + 1 < len(glob_pattern) and glob_pattern[i + 1] == "*":
                if i + 2 < len(glob_pattern) and glob_pattern[i + 2] == "/":
                    result.append("(?:.*/)?")
                    i += 3
                    continue
                result.append(".*")
                i += 2
                continue
            result.append("[^/]*")
        elif c == "?":
            result.append(".")
        elif c == "{":
            end = glob_pattern.find("}", i)
            if end != -1:
                alternatives = glob_pattern[i + 1 : end].split(",")
                result.append("(" + "|".join(re.escape(alt) for alt in alternatives) + ")")
                i = end
            else:
                result.append(re.escape(c))
        elif c in ".^$+[]\\|()":
            result.append("\\" + c)
        else:
            result.append(c)
        i += 1
    return "".join(result) + "$"


def format_grep_matches(
    file_path: str,
    lines: list[str],
    match_line_nums: set[int],
    context_lines: int,
    include_line_numbers: bool = True,
) -> list[str]:
    if not match_line_nums:
        return []

    lines_to_show: set[int] = set()
    for line_num in match_line_nums:
        start = max(0, line_num - context_lines)
        end = min(len(lines) - 1, line_num + context_lines)
        for i in range(start, end + 1):
            lines_to_show.add(i)

    sorted_lines = sorted(lines_to_show)
    output: list[str] = [file_path]
    prev_line = -2

    for line_num in sorted_lines:
        if prev_line >= 0 and line_num > prev_line + 1:
            output.append("--")

        if include_line_numbers:
            marker = ":" if line_num in match_line_nums else "-"
            output.append(f"{line_num + 1}{marker}{lines[line_num]}")
        else:
            output.append(lines[line_num])
        prev_line = line_num

    return output


def truncate_output(text: str, max_chars: int, hint: str) -> str:
    if max_chars <= 0:
        return text
    if len(text) <= max_chars:
        return text

    shown = text[:max_chars]
    total_lines = text.count("\n") + 1
    shown_lines = shown.count("\n") + 1
    return (
        f"{shown}\n"
        f"... (truncated: showing {max_chars} of {len(text)} characters, "
        f"{shown_lines} of {total_lines} lines)\n"
        f"{hint}"
    )


def _fmt_table_line(columns: list[str]) -> str:
    return " | ".join(columns)


def _render_name_list(data: Any) -> str:
    rows = data if isinstance(data, list) else []
    return "\n".join(
        str(row.get("name", ""))
        for row in rows
        if str(row.get("name", "")).strip()
    )


def _render_discover_repos(data: Any) -> str:
    rows = data if isinstance(data, list) else []
    project_names = {
        str(row.get("projectName", "")).strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("projectName", "")).strip()
    }
    if not project_names:
        return _render_name_list(data)

    if len(project_names) == 1:
        return _render_name_list(data)

    lines = [_fmt_table_line(["project", "repo"])]
    lines.extend(
        _fmt_table_line([
            str(row.get("projectName", "")),
            str(row.get("name", "")),
        ])
        for row in rows
        if isinstance(row, dict)
    )
    return "\n".join(lines)


def _render_code_search(data: Any) -> str:
    matches_count = int(data.get("matchesCount", 0))
    result_lines = data.get("results", [])
    lines = [f"matches: {matches_count}"]
    lines.extend(str(entry) for entry in result_lines)
    return "\n".join(lines)


def _render_grep(data: Any) -> str:
    text = str(data.get("text", ""))
    warnings = data.get("warnings") or []
    if warnings:
        warning_block = "\n".join(f"warning: {w}" for w in warnings)
        return f"{text}\n{warning_block}" if text else warning_block
    return text


def _render_pr_list(data: Any) -> str:
    rows = data.get("results", []) if isinstance(data, dict) else []
    lines = [_fmt_table_line(["project", "repo", "pr_id", "status", "title"])]
    lines.extend(
        _fmt_table_line([
            str(row.get("project_name", "")),
            str(row.get("repository_name", "")),
            str(row.get("pr_id", "")),
            str(row.get("status", "")),
            str(row.get("title", "")),
        ])
        for row in rows
    )
    lines.append(f"returned_count: {data.get('returned_count', len(rows))}")
    lines.append(f"has_more: {data.get('has_more', False)}")
    return "\n".join(lines)


def _render_pr_get(data: Any) -> str:
    pr = data.get("pull_request", {}) if isinstance(data, dict) else {}
    threads = data.get("threads", []) if isinstance(data, dict) else []
    return "\n".join([
        f"id: {pr.get('pullRequestId')}",
        f"title: {pr.get('title', '')}",
        f"status: {pr.get('status', '')}",
        f"creator: {(pr.get('createdBy') or {}).get('displayName', '')}",
        f"source_branch: {normalize_branch_name(pr.get('sourceRefName')) or ''}",
        f"target_branch: {normalize_branch_name(pr.get('targetRefName')) or ''}",
        f"comments_threads: {len(threads)}",
    ])


def _render_pr_threads(data: Any) -> str:
    threads = data.get("threads", []) if isinstance(data, dict) else []
    lines = [
        f"pull_request_id: {data.get('pull_request_id')}",
        f"returned_count: {data.get('returned_count', len(threads))}",
        f"total_comments: {data.get('total_comments', 0)}",
    ]
    for thread in threads:
        if not isinstance(thread, dict):
            continue
        header = (
            f"thread {thread.get('id')} status={thread.get('status')} "
            f"comments={thread.get('comment_count', 0)}"
        )
        file_path = thread.get("file_path")
        line_start = thread.get("line_start")
        if file_path and line_start:
            header = f"{header} file={file_path}:{line_start}"
        elif file_path:
            header = f"{header} file={file_path}"
        lines.append(header)
        for comment in thread.get("comments", []):
            if not isinstance(comment, dict):
                continue
            author = str(comment.get("author") or "unknown")
            content = str(comment.get("content") or "").strip().replace("\n", " ")
            if len(content) > 180:
                content = content[:177] + "..."
            lines.append(f"  - {author}: {content}")
    return "\n".join(lines)


def _render_build_logs(data: Any) -> str:
    metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
    logs = data.get("logs", []) if isinstance(data, dict) else []
    lines = [
        f"build_id: {metadata.get('build_id')}",
        f"status: {metadata.get('status')}",
        f"result: {metadata.get('result')}",
        f"definition: {metadata.get('definition_name')}",
        "logs:",
    ]
    lines.extend(
        _fmt_table_line([
            str(log.get("id", "")),
            str(log.get("line_count", "")),
            str(log.get("stage_name", "")),
            str(log.get("job_name", "")),
            str(log.get("step_name", "")),
        ])
        for log in logs
    )
    return "\n".join(lines)


def _render_board_ticket(data: Any) -> str:
    fields = (data.get("fields") or {}) if isinstance(data, dict) else {}
    return "\n".join([
        f"id: {data.get('id')}",
        f"type: {fields.get('System.WorkItemType', '')}",
        f"state: {fields.get('System.State', '')}",
        f"title: {fields.get('System.Title', '')}",
    ])


def _render_board_table(data: Any) -> str:
    rows = data.get("results", []) if isinstance(data, dict) else []
    lines = [_fmt_table_line(["id", "type", "state", "title"])]
    for row in rows:
        fields = row.get("fields", {}) if isinstance(row, dict) else {}
        lines.append(
            _fmt_table_line([
                str(row.get("id", "")),
                str(row.get("type") or fields.get("System.WorkItemType", "")),
                str(row.get("state") or fields.get("System.State", "")),
                str(row.get("title") or fields.get("System.Title", "")),
            ])
        )
    if isinstance(data, dict):
        if "returned_count" in data:
            lines.append(f"returned_count: {data['returned_count']}")
        if "has_more" in data:
            lines.append(f"has_more: {data['has_more']}")
    return "\n".join(lines)


_RENDER_DISPATCH: dict[str, Any] = {
    "orgs": _render_name_list,
    "repos": _render_discover_repos,
    "code.search": _render_code_search,
    "code.grep": _render_grep,
    "pipelines.logs.grep": _render_grep,
    "prs.list": _render_pr_list,
    "prs.get": _render_pr_get,
    "prs.threads": _render_pr_threads,
    "pipelines.logs.list": _render_build_logs,
    "stories.get": _render_board_ticket,
    "stories.search": _render_board_table,
    "stories.mine": _render_board_table,
}


def _render_single(command: str, data: Any) -> str:
    renderer = _RENDER_DISPATCH.get(command)
    if renderer is not None:
        return renderer(data)
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True)


def _render_provider_grouped(command: str, payload: dict[str, Any]) -> str:
    providers = payload.get("providers", {})
    if not isinstance(providers, dict):
        return _render_single(command, payload)

    summary = payload.get("summary", {})
    queried = summary.get("queried") if isinstance(summary, dict) else []
    ordered = queried if isinstance(queried, list) and queried else list(providers.keys())

    # Flatten single-provider results for cleaner developer UX.
    if len(ordered) == 1:
        provider = ordered[0]
        entry = providers.get(provider, {})
        if not isinstance(entry, dict):
            return ""

        if not bool(entry.get("ok", False)):
            error = entry.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else error
            return f"error: {message}".rstrip()

        provider_data = entry.get("data")
        lines: list[str] = []
        rendered = _render_single(command, provider_data)
        if rendered.strip():
            lines.append(rendered)

        warnings = entry.get("warnings") or []
        if command not in {"code.grep", "pipelines.logs.grep"}:
            for warning in warnings if isinstance(warnings, list) else []:
                lines.append(f"warning: {warning}")
        if bool(entry.get("partial", False)):
            lines.append("partial: true")
        return "\n".join(lines).rstrip()

    output_lines: list[str] = []

    for provider in ordered:
        entry = providers.get(provider, {})
        if not isinstance(entry, dict):
            continue

        if not bool(entry.get("ok", False)):
            output_lines.append(f"[{provider}]")
            error = entry.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else error
            output_lines.append(f"error: {message}")
            output_lines.append("")
            continue

        provider_data = entry.get("data")
        if command == "code.search":
            matches_count = 0
            result_lines: list[Any] = []
            if isinstance(provider_data, dict):
                raw_matches = provider_data.get("matchesCount", 0)
                try:
                    matches_count = int(raw_matches)
                except (TypeError, ValueError):
                    matches_count = 0
                raw_results = provider_data.get("results", [])
                if isinstance(raw_results, list):
                    result_lines = raw_results
            output_lines.append(f"[{provider}] matches: {matches_count}")
            output_lines.extend(str(entry_line) for entry_line in result_lines)
        else:
            output_lines.append(f"[{provider}]")
            rendered = _render_single(command, provider_data)
            if rendered.strip():
                output_lines.append(rendered)

        warnings = entry.get("warnings") or []
        if command not in {"code.grep", "pipelines.logs.grep"}:
            for warning in warnings if isinstance(warnings, list) else []:
                output_lines.append(f"warning: {warning}")
        if bool(entry.get("partial", False)):
            output_lines.append("partial: true")
        output_lines.append("")

    return "\n".join(output_lines).rstrip()


def render_text(command: str, data: Any) -> str:
    if isinstance(data, dict) and "providers" in data and "summary" in data:
        return _render_provider_grouped(command, data)
    return _render_single(command, data)
