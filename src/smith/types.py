from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict


class RemoteError(TypedDict):
    code: str
    message: str


class RemoteEntry(TypedDict):
    ok: bool
    data: Any
    warnings: list[str]
    partial: bool
    error: RemoteError | None


class FanoutSummary(TypedDict):
    requested_remote: str
    queried: list[str]
    succeeded: list[str]
    failed: list[str]


class FanoutResult(TypedDict):
    remotes: dict[str, RemoteEntry]
    summary: FanoutSummary


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 2
    backoff_seconds: float = 0.4
