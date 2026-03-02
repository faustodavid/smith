from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from requests.adapters import HTTPAdapter


def configure_http_session(session: Any, *, pool_connections: int, pool_maxsize: int) -> None:
    if not hasattr(session, "mount"):
        return
    try:
        adapter = HTTPAdapter(
            pool_connections=pool_connections,
            pool_maxsize=pool_maxsize,
            max_retries=0,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
    except Exception:
        # Keep behavior safe for mocked/injected session objects.
        return


def is_retryable_get_status(status_code: int) -> bool:
    return status_code in {429, 500, 502, 503, 504}


def parse_retry_after_seconds(response: Any) -> float | None:
    headers = getattr(response, "headers", {}) or {}
    raw = headers.get("Retry-After")
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit():
        return max(0.0, float(text))
    try:
        retry_at = parsedate_to_datetime(text)
    except Exception:
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    return max(0.0, (retry_at - now).total_seconds())
