from __future__ import annotations

import os
import re
from datetime import UTC, datetime


def normalize_path(path: str | None) -> str:
    if not path:
        return "/"
    if path.startswith("/"):
        return path
    return "/" + path


def parse_iso_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def match_all_pattern(pattern: str) -> bool:
    return pattern in (".*", "^.*$", ".*$", "^.*")


def slice_lines(
    lines: list[str],
    *,
    from_line: int | None,
    to_line: int | None,
) -> list[str]:
    if from_line is None and to_line is None:
        return lines
    start_idx = (from_line - 1) if from_line and from_line > 0 else 0
    end_idx = to_line if to_line and to_line > 0 else len(lines)
    return lines[start_idx:end_idx]


def compile_search_pattern(
    pattern: str,
    *,
    case_insensitive: bool,
) -> tuple[re.Pattern[str] | None, str | None]:
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        return re.compile(pattern, flags), None
    except re.error as exc:
        return None, f"Error: Invalid regex pattern - {exc}"


def parse_bool_env(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def parse_int_env(
    name: str,
    *,
    default: int,
    min_value: int,
    max_value: int,
) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, parsed))
