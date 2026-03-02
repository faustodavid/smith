from __future__ import annotations

from typing import Any


def execute_board_ticket(client: Any, **kwargs: Any) -> dict[str, Any]:
    return client.execute_board_ticket(**kwargs)


def execute_board_list(client: Any, **kwargs: Any) -> dict[str, Any]:
    return client.execute_board_list(**kwargs)


def execute_board_search(client: Any, **kwargs: Any) -> dict[str, Any]:
    return client.execute_board_search(**kwargs)


def execute_board_mine(client: Any, **kwargs: Any) -> dict[str, Any]:
    return client.execute_board_mine(**kwargs)
