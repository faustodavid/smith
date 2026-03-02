from __future__ import annotations

from typing import Any


def execute_code_search(client: Any, **kwargs: Any) -> dict[str, Any]:
    return client.execute_code_search(**kwargs)


def execute_code_grep(client: Any, **kwargs: Any) -> dict[str, Any]:
    return client.execute_code_grep(**kwargs)
