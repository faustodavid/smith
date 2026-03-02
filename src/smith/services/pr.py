from __future__ import annotations

from typing import Any


def execute_pr_list(client: Any, **kwargs: Any) -> dict[str, Any]:
    return client.execute_pr_list(**kwargs)


def execute_pr_get(client: Any, **kwargs: Any) -> dict[str, Any]:
    return client.execute_pr_get(**kwargs)


def execute_pr_threads(client: Any, **kwargs: Any) -> dict[str, Any]:
    return client.execute_pr_threads(**kwargs)
