from __future__ import annotations

from typing import Literal

ProviderName = Literal["azdo", "github", "all"]


def normalize_provider(provider: str | None) -> ProviderName:
    normalized = (provider or "azdo").strip().lower()
    if normalized not in {"azdo", "github", "all"}:
        raise ValueError("provider must be one of: azdo, github, all")
    return normalized  # type: ignore[return-value]


def resolve_providers(provider: str | None) -> list[str]:
    normalized = normalize_provider(provider)
    if normalized == "all":
        return ["github", "azdo"]
    return [normalized]


def normalize_single_provider(provider: str | None, *, command: str) -> str:
    normalized = normalize_provider(provider)
    if normalized == "all":
        raise ValueError(f"{command} does not support provider 'all'. Use azdo or github.")
    return normalized
