from __future__ import annotations

from smith import SmithApiError, SmithAuthError, SmithClient, SmithError


def test_public_api_exports() -> None:
    assert SmithClient is not None
    assert SmithError is not None
    assert SmithAuthError is not None
    assert SmithApiError is not None
