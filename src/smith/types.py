from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypedDict

ProviderName = Literal["azdo", "github", "gitlab", "all"]


class ProviderError(TypedDict):
    code: str
    message: str


class ProviderEntry(TypedDict):
    ok: bool
    data: Any
    warnings: list[str]
    partial: bool
    error: ProviderError | None


class FanoutSummary(TypedDict):
    requested_provider: str
    queried: list[str]
    succeeded: list[str]
    failed: list[str]


class FanoutResult(TypedDict):
    providers: dict[str, ProviderEntry]
    summary: FanoutSummary


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 2
    backoff_seconds: float = 0.4
