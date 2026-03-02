from __future__ import annotations

from typing import Any


class GitHubProvider:
    """Adapter surface for GitHub provider operations."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def list_projects(self) -> list[dict[str, Any]]:
        return self._client._github_list_projects()

    def list_repositories(self) -> list[dict[str, Any]]:
        return self._client._github_list_repositories()
