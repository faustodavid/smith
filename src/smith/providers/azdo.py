from __future__ import annotations

from typing import Any


class AzdoProvider:
    """Adapter surface for Azure DevOps provider operations."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def list_projects(self) -> list[dict[str, Any]]:
        return self._client.list_projects()

    def list_repositories(self, project: str) -> list[dict[str, Any]]:
        return self._client.list_repositories(project)
