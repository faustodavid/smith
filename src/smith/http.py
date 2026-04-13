from __future__ import annotations

import importlib
import logging
import threading
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

_NATIVE_TLS_TRUST_CONFIGURED = False
_NATIVE_TLS_TRUST_LOCK = threading.Lock()


def configure_native_tls_trust() -> None:
    global _NATIVE_TLS_TRUST_CONFIGURED

    if _NATIVE_TLS_TRUST_CONFIGURED:
        return

    with _NATIVE_TLS_TRUST_LOCK:
        if _NATIVE_TLS_TRUST_CONFIGURED:
            return

        try:
            truststore = importlib.import_module("truststore")
        except ImportError:
            logger.debug("Native TLS trust store support is unavailable; proceeding with default SSL context.")
            _NATIVE_TLS_TRUST_CONFIGURED = True
            return

        try:
            truststore.inject_into_ssl()
        except Exception as exc:
            logger.debug("Could not configure native TLS trust store: %s", exc)
        _NATIVE_TLS_TRUST_CONFIGURED = True


def configure_http_session(session: Any, *, pool_connections: int, pool_maxsize: int) -> None:
    configure_native_tls_trust()
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
    except (TypeError, AttributeError) as exc:
        logger.debug("Could not configure HTTP adapter (mocked session?): %s", exc)


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
    except (TypeError, ValueError) as exc:
        logger.debug("Could not parse Retry-After header %r: %s", text, exc)
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    return max(0.0, (retry_at - now).total_seconds())


def parse_rate_limit_reset_seconds(response: Any) -> float | None:
    headers = getattr(response, "headers", {}) or {}
    raw = headers.get("X-RateLimit-Reset")
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        reset_epoch = float(text)
    except (TypeError, ValueError) as exc:
        logger.debug("Could not parse X-RateLimit-Reset header %r: %s", text, exc)
        return None
    now_epoch = datetime.now(UTC).timestamp()
    return max(0.0, reset_epoch - now_epoch)
