from __future__ import annotations

import re
from dataclasses import dataclass, field
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
    _compiled_pattern: re.Pattern[str] | None = field(default=None, init=False, repr=False, compare=False)

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
        instance = cls(
            grep=normalized_grep,
            skip=normalized_skip,
            take=normalized_take,
            requested_take=requested_take,
            warnings=warnings,
        )
        if normalized_grep:
            compiled, error = compile_search_pattern(normalized_grep, case_insensitive=False)
            if compiled is None:
                raise ValueError(error or "Error: Invalid regex pattern")
            object.__setattr__(instance, "_compiled_pattern", compiled)
        return instance

    @property
    def required_matches(self) -> int:
        return self.skip + self.take + 1

    def compile_grep(self) -> re.Pattern[str] | None:
        return self._compiled_pattern

    def is_simple_substring(self) -> bool:
        if not self.grep:
            return False
        return not bool(re.search(r'[.^$*+?{}\[\]\\|()]', self.grep))

    def server_search_term(self) -> str | None:
        if not self.is_simple_substring() or self.grep is None:
            return None
        return self.grep

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
