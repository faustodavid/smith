from __future__ import annotations

from typing import Any


def execute_build_logs(client: Any, **kwargs: Any) -> dict[str, Any]:
    return client.execute_build_logs(**kwargs)


def execute_build_grep(client: Any, **kwargs: Any) -> dict[str, Any]:
    return client.execute_build_grep(**kwargs)
