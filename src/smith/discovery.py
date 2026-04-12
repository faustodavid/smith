from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from smith.utils import compile_search_pattern

DEFAULT_DISCOVERY_TAKE = 50
MAX_DISCOVERY_TAKE = 500


@dataclass(frozen=True)
class DiscoveryQuery:
    grep: str | None = None
    skip: int = 0
    take: int = DEFAULT_DISCOVERY_TAKE
    requested_take: int = DEFAULT_DISCOVERY_TAKE
    warnings: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        grep: str | None = None,
        skip: int | None = None,
        take: int | None = None,
    ) -> "DiscoveryQuery":
        normalized_grep = str(grep or "").strip() or None
        normalized_skip = max(0, int(skip or 0))
        requested_take = int(take if take is not None else DEFAULT_DISCOVERY_TAKE)
        normalized_take = min(MAX_DISCOVERY_TAKE, max(1, requested_take))
        warnings: tuple[str, ...] = ()
        if requested_take > MAX_DISCOVERY_TAKE:
            warnings = (f"`--take` capped at {MAX_DISCOVERY_TAKE}.",)
        return cls(
            grep=normalized_grep,
            skip=normalized_skip,
            take=normalized_take,
            requested_take=requested_take,
            warnings=warnings,
        )

    @property
    def required_matches(self) -> int:
        return self.skip + self.take + 1

    def compile_grep(self) -> re.Pattern[str] | None:
        if not self.grep:
            return None
        compiled, error = compile_search_pattern(self.grep, case_insensitive=False)
        if compiled is None:
            raise ValueError(error or "Error: Invalid regex pattern")
        return compiled

    def truncation_warning(self, *, subject: str) -> str:
        return f"showing {self.take} matching {subject}; use --skip/--take to see more."


def build_discovery_payload(
    *,
    rows: list[dict[str, Any]],
    query: DiscoveryQuery,
    has_more: bool,
    subject: str,
) -> dict[str, Any]:
    warnings = list(query.warnings)
    if has_more:
        warnings.append(query.truncation_warning(subject=subject))
    return {
        "results": [dict(row) for row in rows],
        "returned_count": len(rows),
        "has_more": has_more,
        "warnings": warnings,
        "partial": bool(warnings),
    }
