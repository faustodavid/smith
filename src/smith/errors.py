from __future__ import annotations


class SmithError(Exception):
    """Base error for smith."""


class SmithAuthError(SmithError):
    """Auth-specific failure."""


class SmithApiError(SmithError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
