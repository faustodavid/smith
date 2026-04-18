#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from typing import Any

from toon_format import encode as toon_encode


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


def _group_contiguous_lines(sorted_line_nums: list[int]) -> list[list[int]]:
    blocks: list[list[int]] = []
    current_block: list[int] = []
    for line_num in sorted_line_nums:
        if current_block and line_num > current_block[-1] + 1:
            blocks.append(current_block)
            current_block = []
        current_block.append(line_num)
    if current_block:
        blocks.append(current_block)
    return blocks


def format_grep_matches(
    file_path: str,
    lines: list[str],
    match_line_nums: set[int],
    context_lines: int,
    include_line_numbers: bool = True,
    *,
    line_offset: int = 0,
    reverse: bool = False,
) -> list[str]:
    if not match_line_nums:
        return []

    lines_to_show: set[int] = set()
    for line_num in match_line_nums:
        start = max(0, line_num - context_lines)
        end = min(len(lines) - 1, line_num + context_lines)
        for i in range(start, end + 1):
            lines_to_show.add(i)

    blocks = _group_contiguous_lines(sorted(lines_to_show))
    if reverse:
        blocks.reverse()

    output: list[str] = [file_path]
    for block_index, block in enumerate(blocks):
        if block_index > 0:
            output.append("--")
        for line_num in block:
            if include_line_numbers:
                marker = ":" if line_num in match_line_nums else "-"
                output.append(f"{line_num + 1 + line_offset}{marker}{lines[line_num]}")
            else:
                output.append(lines[line_num])

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


_GITLAB_CODE_SEARCH_LOWER_BOUND_WARNING = (
    "GitLab search did not provide an exact total; `matchesCount` is a lower bound. "
    "Narrow with `--repo group/project` for exact counts."
)


def _render_name_list(data: Any) -> str:
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        rows = data.get("results", [])
    else:
        rows = data if isinstance(data, list) else []
    return "\n".join(
        str(row.get("name", ""))
        for row in rows
        if str(row.get("name", "")).strip()
    )


def _render_discover_repos(data: Any) -> str:
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        rows = data.get("results", [])
    else:
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


def _code_search_summary_line(matches_count: int, shown_count: int, *, lower_bound: bool = False) -> str:
    suffix = "+" if lower_bound else ""
    if shown_count < matches_count or lower_bound:
        return f"matches: {matches_count}{suffix} (showing {shown_count})"
    return f"matches: {matches_count}{suffix}"


def _render_code_search(data: Any) -> str:
    matches_count = int(data.get("matchesCount", 0))
    lower_bound = bool(data.get("matchesCountLowerBound", False))
    raw_results = data.get("results", [])
    result_lines = raw_results if isinstance(raw_results, list) else []
    lines = [_code_search_summary_line(matches_count, len(result_lines), lower_bound=lower_bound)]
    lines.extend(str(entry) for entry in result_lines)
    return "\n".join(lines)


def _visible_remote_warnings(command: str, remote_data: Any, warnings: Any) -> list[str]:
    warning_list = [str(warning) for warning in warnings if str(warning).strip()] if isinstance(warnings, list) else []
    if command != "code.search":
        return warning_list

    lower_bound = False
    if isinstance(remote_data, dict):
        lower_bound = bool(remote_data.get("matchesCountLowerBound", False))
    if lower_bound:
        return [warning for warning in warning_list if warning != _GITLAB_CODE_SEARCH_LOWER_BOUND_WARNING]
    return warning_list


def _render_grep(data: Any) -> str:
    text = str(data.get("text", ""))
    warnings = data.get("warnings") or []
    if warnings:
        warning_block = "\n".join(f"warning: {w}" for w in warnings)
        return f"{text}\n{warning_block}" if text else warning_block
    return text


def _render_cache_clean(data: Any) -> str:
    cleaned = data.get("cleaned", []) if isinstance(data, dict) else []
    missing = data.get("missing", []) if isinstance(data, dict) else []
    lines: list[str] = []

    if cleaned:
        lines.append("cleaned:")
        lines.extend(str(path) for path in cleaned)

    if missing:
        lines.append("missing:")
        lines.extend(str(path) for path in missing)

    return "\n".join(lines)


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
    diffs = data.get("diffs", {}) if isinstance(data, dict) else {}
    lines = [
        f"id: {pr.get('pullRequestId')}",
        f"title: {pr.get('title', '')}",
        f"status: {pr.get('status', '')}",
        f"creator: {(pr.get('createdBy') or {}).get('displayName', '')}",
        f"source_branch: {normalize_branch_name(pr.get('sourceRefName')) or ''}",
        f"target_branch: {normalize_branch_name(pr.get('targetRefName')) or ''}",
        f"comments_threads: {len(threads)}",
    ]
    if isinstance(diffs, dict):
        rendered_diffs: list[str] = []
        for path, diff in diffs.items():
            path_text = str(path or "").strip()
            diff_text = str(diff or "").rstrip("\n")
            if not path_text or not diff_text:
                continue
            rendered_diffs.append(f"diff: {path_text}")
            rendered_diffs.extend(diff_text.splitlines())
        if rendered_diffs:
            lines.append("diffs:")
            lines.extend(rendered_diffs)
    return "\n".join(lines)


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


def _render_youtrack_ticket(data: Any) -> str:
    if not isinstance(data, dict):
        return _render_board_ticket(data)

    raw_metadata = data.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    raw_comments = data.get("comments")
    comments = raw_comments if isinstance(raw_comments, list) else []
    raw_links = data.get("links")
    links = raw_links if isinstance(raw_links, list) else []
    raw_attachments = data.get("attachments")
    attachments = raw_attachments if isinstance(raw_attachments, list) else []
    raw_timeline = data.get("timeline")
    timeline = raw_timeline if isinstance(raw_timeline, list) else []

    lines = [
        f"id: {data.get('id')}",
        f"title: {data.get('title') or ((data.get('fields') or {}).get('System.Title', ''))}",
        f"url: {data.get('url') or ''}",
    ]

    core_metadata_keys = [
        "Project",
        "Type",
        "State",
        "Priority",
        "Reporter",
        "Updater",
        "Created",
        "Updated",
        "Resolved",
        "Votes",
        "Comments",
        "Tags",
    ]
    for key in core_metadata_keys:
        value = str(metadata.get(key) or "").strip()
        if value:
            lines.append(f"{key.lower()}: {value}")

    extra_metadata_lines = []
    for key, value in metadata.items():
        if key in core_metadata_keys:
            continue
        rendered = str(value or "").strip() or "-"
        extra_metadata_lines.append(f"{key}: {rendered}")
    if extra_metadata_lines:
        lines.extend(["", "--- metadata ---", *extra_metadata_lines])

    description = str(data.get("description") or "").strip()
    if description:
        lines.extend(["", "--- description ---", description])

    if attachments:
        lines.extend(["", "--- attachments ---"])
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            details = [str(attachment.get("name") or "").strip()]
            mime_type = str(attachment.get("mimeType") or "").strip()
            if mime_type:
                details.append(mime_type)
            size = attachment.get("size")
            if size not in (None, ""):
                details.append(f"{size} bytes")
            url = str(attachment.get("url") or "").strip()
            line = " | ".join(part for part in details if part)
            if url:
                line = f"{line} | {url}" if line else url
            if line:
                lines.append(line)

    if links:
        lines.extend(["", "--- related items ---"])
        for link in links:
            if not isinstance(link, dict):
                continue
            link_name = str(link.get("type") or "Related").strip()
            raw_issue_entries = link.get("issues")
            issue_entries = raw_issue_entries if isinstance(raw_issue_entries, list) else []
            rendered_issues = []
            for issue in issue_entries:
                if not isinstance(issue, dict):
                    continue
                issue_id = str(issue.get("id") or "").strip()
                summary = str(issue.get("summary") or "").strip()
                rendered_issues.append(f"{issue_id} {summary}".strip())
            if rendered_issues:
                lines.append(f"{link_name}: {' | '.join(rendered_issues)}")

    if comments:
        lines.extend(["", f"--- comments ({len(comments)}) ---"])
        for index, comment in enumerate(comments, start=1):
            if not isinstance(comment, dict):
                continue
            header_bits = [
                f"[{index}]",
                str(comment.get("author_display") or "unknown"),
                str(comment.get("created") or "-"),
            ]
            raw_reactions = comment.get("reactions")
            reactions = raw_reactions if isinstance(raw_reactions, list) else []
            reaction_summary = ", ".join(
                " ".join(
                    part
                    for part in [
                        str(reaction.get("reaction") or "").strip(),
                        (
                            f"by {reaction.get('author_display')}"
                            if str(reaction.get("author_display") or "").strip()
                            else ""
                        ),
                    ]
                    if part
                )
                for reaction in reactions
                if isinstance(reaction, dict)
            )
            if reaction_summary:
                header_bits.append(f"[reactions: {reaction_summary}]")
            lines.append(" ".join(part for part in header_bits if part))
            comment_text = str(comment.get("text") or "").strip()
            if comment_text:
                lines.append(comment_text)
            raw_comment_attachments = comment.get("attachments")
            comment_attachments = raw_comment_attachments if isinstance(raw_comment_attachments, list) else []
            for attachment in comment_attachments:
                if not isinstance(attachment, dict):
                    continue
                attachment_name = str(attachment.get("name") or "").strip()
                attachment_url = str(attachment.get("url") or "").strip()
                if attachment_name or attachment_url:
                    lines.append(f"attachment: {attachment_name} {attachment_url}".strip())
            lines.append("")
        while lines and lines[-1] == "":
            lines.pop()

    if timeline:
        lines.extend(["", f"--- timeline ({len(timeline)}) ---"])
        for entry in timeline:
            if not isinstance(entry, dict):
                continue
            timestamp = str(entry.get("timestamp") or "-")
            author = str(entry.get("author_display") or "unknown")
            action = str(entry.get("action") or "updated issue")
            lines.append(f"{timestamp} {author}: {action}")

    return "\n".join(lines)


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


def _render_story_ticket(data: Any) -> str:
    if isinstance(data, dict) and data.get("provider") == "youtrack":
        return _render_youtrack_ticket(data)
    return _render_board_ticket(data)


def _render_story_table(data: Any) -> str:
    if not isinstance(data, dict) or data.get("provider") != "youtrack":
        return _render_board_table(data)

    rows = data.get("results", []) if isinstance(data.get("results"), list) else []
    lines = [_fmt_table_line(["id", "project", "type", "state", "title"])]
    for row in rows:
        if not isinstance(row, dict):
            continue
        lines.append(
            _fmt_table_line([
                str(row.get("id", "")),
                str(row.get("project") or row.get("project_name") or ""),
                str(row.get("type", "")),
                str(row.get("state", "")),
                str(row.get("title", "")),
            ])
        )
    if "returned_count" in data:
        lines.append(f"returned_count: {data['returned_count']}")
    if "has_more" in data:
        lines.append(f"has_more: {data['has_more']}")
    return "\n".join(lines)


_CONFIG_REMOTE_EXTRA_FIELDS = ("org", "host", "token_env", "api_url")
_CONFIG_LIST_FIELD_ORDER = ("name", "provider", "enabled", "org", "host")


def _render_config_list(data: Any) -> str:
    remotes = data.get("remotes", []) if isinstance(data, dict) else []
    if not isinstance(remotes, list):
        remotes = []

    normalized: list[dict[str, Any]] = []
    for entry in remotes:
        if not isinstance(entry, dict):
            continue
        row: dict[str, Any] = {}
        for field in _CONFIG_LIST_FIELD_ORDER:
            value = entry.get(field)
            if field == "enabled":
                row[field] = bool(value)
            elif field in ("name", "provider"):
                row[field] = str(value or "")
            else:
                row[field] = value if value else None
        normalized.append(row)

    return toon_encode({"remotes": normalized})


def _render_config_show(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    ordered_keys = ("name", "provider", "enabled", *_CONFIG_REMOTE_EXTRA_FIELDS)
    lines: list[str] = []
    for key in ordered_keys:
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = str(value)
        if rendered == "":
            continue
        lines.append(f"{key}: {rendered}")
    return "\n".join(lines)


_RENDER_DISPATCH: dict[str, Any] = {
    "orgs": _render_name_list,
    "groups": _render_name_list,
    "groups.list": _render_name_list,
    "repos": _render_discover_repos,
    "code.search": _render_code_search,
    "code.grep": _render_grep,
    "cache.clean": _render_cache_clean,
    "pipelines.logs.grep": _render_grep,
    "prs.search": _render_pr_list,
    "prs.list": _render_pr_list,
    "prs.get": _render_pr_get,
    "prs.threads": _render_pr_threads,
    "pipelines.logs.list": _render_build_logs,
    "stories.get": _render_story_ticket,
    "stories.search": _render_story_table,
    "stories.mine": _render_story_table,
    "config.list": _render_config_list,
    "config.show": _render_config_show,
}


def _render_single(command: str, data: Any) -> str:
    renderer = _RENDER_DISPATCH.get(command)
    if renderer is not None:
        return renderer(data)
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True)


def _render_remote_grouped(command: str, payload: dict[str, Any]) -> str:
    remotes = payload.get("remotes", {})
    if not isinstance(remotes, dict):
        return _render_single(command, payload)

    summary = payload.get("summary", {})
    queried = summary.get("queried") if isinstance(summary, dict) else []
    ordered = queried if isinstance(queried, list) and queried else list(remotes.keys())

    # Flatten single-remote results for cleaner developer UX.
    if len(ordered) == 1:
        remote_name = ordered[0]
        entry = remotes.get(remote_name, {})
        if not isinstance(entry, dict):
            return ""

        if not bool(entry.get("ok", False)):
            error = entry.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else error
            return f"error: {message}".rstrip()

        remote_data = entry.get("data")
        lines: list[str] = []
        rendered = _render_single(command, remote_data)
        if rendered.strip():
            lines.append(rendered)

        warnings = _visible_remote_warnings(command, remote_data, entry.get("warnings") or [])
        if command not in {"code.grep", "pipelines.logs.grep"}:
            for warning in warnings:
                lines.append(f"warning: {warning}")
        return "\n".join(lines).rstrip()

    output_lines: list[str] = []

    for remote_name in ordered:
        entry = remotes.get(remote_name, {})
        if not isinstance(entry, dict):
            continue

        if not bool(entry.get("ok", False)):
            output_lines.append(f"[{remote_name}]")
            error = entry.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else error
            output_lines.append(f"error: {message}")
            output_lines.append("")
            continue

        remote_data = entry.get("data")
        if command == "code.search":
            matches_count = 0
            lower_bound = False
            result_lines: list[Any] = []
            if isinstance(remote_data, dict):
                raw_matches = remote_data.get("matchesCount", 0)
                try:
                    matches_count = int(raw_matches)
                except (TypeError, ValueError):
                    matches_count = 0
                lower_bound = bool(remote_data.get("matchesCountLowerBound", False))
                raw_results = remote_data.get("results", [])
                if isinstance(raw_results, list):
                    result_lines = raw_results
            output_lines.append(
                f"[{remote_name}] {_code_search_summary_line(matches_count, len(result_lines), lower_bound=lower_bound)}"
            )
            output_lines.extend(str(entry_line) for entry_line in result_lines)
        else:
            output_lines.append(f"[{remote_name}]")
            rendered = _render_single(command, remote_data)
            if rendered.strip():
                output_lines.append(rendered)

        warnings = _visible_remote_warnings(command, remote_data, entry.get("warnings") or [])
        if command not in {"code.grep", "pipelines.logs.grep"}:
            for warning in warnings:
                output_lines.append(f"warning: {warning}")
        output_lines.append("")

    return "\n".join(output_lines).rstrip()


def render_text(command: str, data: Any) -> str:
    if isinstance(data, dict) and "remotes" in data and "summary" in data:
        return _render_remote_grouped(command, data)
    return _render_single(command, data)
