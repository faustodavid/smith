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


def render_text(command: str, data: Any) -> str:
    if command == "projects.list":
        rows = data if isinstance(data, list) else []
        lines = [
            str(row.get("name", ""))
            for row in rows
            if str(row.get("name", "")).strip()
        ]
        return "\n".join(lines)

    if command == "repos.list":
        rows = data if isinstance(data, list) else []
        lines = [
            str(row.get("name", ""))
            for row in rows
            if str(row.get("name", "")).strip()
        ]
        return "\n".join(lines)

    if command == "code.search":
        matches_count = int(data.get("matchesCount", 0))
        result_lines = data.get("results", [])
        lines = [f"matches: {matches_count}"]
        lines.extend(str(entry) for entry in result_lines)
        return "\n".join(lines)

    if command in {"code.grep", "build.grep"}:
        text = str(data.get("text", ""))
        warnings = data.get("warnings") or []
        if warnings:
            warning_block = "\n".join(f"warning: {w}" for w in warnings)
            if text:
                return f"{text}\n{warning_block}"
            return warning_block
        return text

    if command == "pr.list":
        rows = data.get("results", []) if isinstance(data, dict) else []
        lines = [_fmt_table_line(["project", "repo", "pr_id", "status", "title"])]
        lines.extend(
            _fmt_table_line(
                [
                    str(row.get("project_name", "")),
                    str(row.get("repository_name", "")),
                    str(row.get("pr_id", "")),
                    str(row.get("status", "")),
                    str(row.get("title", "")),
                ]
            )
            for row in rows
        )
        lines.append(f"returned_count: {data.get('returned_count', len(rows))}")
        lines.append(f"has_more: {data.get('has_more', False)}")
        return "\n".join(lines)

    if command == "pr.get":
        pr = data.get("pull_request", {}) if isinstance(data, dict) else {}
        threads = data.get("threads", []) if isinstance(data, dict) else []
        lines = [
            f"id: {pr.get('pullRequestId')}",
            f"title: {pr.get('title', '')}",
            f"status: {pr.get('status', '')}",
            f"creator: {(pr.get('createdBy') or {}).get('displayName', '')}",
            f"source_branch: {normalize_branch_name(pr.get('sourceRefName')) or ''}",
            f"target_branch: {normalize_branch_name(pr.get('targetRefName')) or ''}",
            f"comments_threads: {len(threads)}",
        ]
        return "\n".join(lines)

    if command == "pr.threads":
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

            comments = thread.get("comments", [])
            for comment in comments:
                if not isinstance(comment, dict):
                    continue
                author = str(comment.get("author") or "unknown")
                content = str(comment.get("content") or "").strip().replace("\n", " ")
                if len(content) > 180:
                    content = content[:177] + "..."
                lines.append(f"  - {author}: {content}")
        return "\n".join(lines)

    if command == "build.logs":
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
            _fmt_table_line(
                [
                    str(log.get("id", "")),
                    str(log.get("line_count", "")),
                    str(log.get("stage_name", "")),
                    str(log.get("job_name", "")),
                    str(log.get("step_name", "")),
                ]
            )
            for log in logs
        )
        return "\n".join(lines)

    if command in {"board.ticket", "board.list", "board.search", "board.mine"}:
        if command == "board.ticket":
            fields = (data.get("fields") or {}) if isinstance(data, dict) else {}
            return "\n".join(
                [
                    f"id: {data.get('id')}",
                    f"type: {fields.get('System.WorkItemType', '')}",
                    f"state: {fields.get('System.State', '')}",
                    f"title: {fields.get('System.Title', '')}",
                ]
            )

        rows = data.get("results", []) if isinstance(data, dict) else []
        lines = [_fmt_table_line(["id", "type", "state", "title"])]
        for row in rows:
            fields = row.get("fields", {}) if isinstance(row, dict) else {}
            lines.append(
                _fmt_table_line(
                    [
                        str(row.get("id", row.get("id") if isinstance(row, dict) else "")),
                        str(
                            row.get("type")
                            or fields.get("System.WorkItemType", "")
                        ),
                        str(row.get("state") or fields.get("System.State", "")),
                        str(row.get("title") or fields.get("System.Title", "")),
                    ]
                )
            )
        if isinstance(data, dict):
            if "returned_count" in data:
                lines.append(f"returned_count: {data['returned_count']}")
            if "has_more" in data:
                lines.append(f"has_more: {data['has_more']}")
        return "\n".join(lines)

    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True)
