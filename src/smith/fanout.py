from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from smith.errors import SmithApiError, SmithAuthError

logger = logging.getLogger(__name__)


def run_fanout(
    *,
    remotes: list[str],
    requested_remote: str,
    operations: dict[str, Callable[[], Any]],
    remote_entry_success: Callable[[Any], dict[str, Any]],
    remote_entry_error: Callable[[str, str], dict[str, Any]],
) -> dict[str, Any]:
    remote_results: dict[str, dict[str, Any]] = {}
    succeeded: list[str] = []
    failed: list[str] = []

    def run_remote_operation(remote_name: str) -> tuple[dict[str, Any], bool]:
        operation = operations.get(remote_name)
        if operation is None:
            return (
                remote_entry_error(
                    "unsupported_remote",
                    f"Remote '{remote_name}' is not supported for this command.",
                ),
                False,
            )

        logger.debug("Running remote operation: %s", remote_name)
        try:
            payload = operation()
            logger.debug("Remote %s succeeded", remote_name)
            return remote_entry_success(payload), True
        except ValueError as exc:
            logger.debug("Remote %s raised ValueError: %s", remote_name, exc)
            return remote_entry_error("invalid_args", str(exc)), False
        except SmithAuthError as exc:
            logger.debug("Remote %s auth failure: %s", remote_name, exc)
            return remote_entry_error("auth_failure", str(exc)), False
        except SmithApiError as exc:
            logger.debug("Remote %s API error: %s", remote_name, exc)
            return remote_entry_error("api_error", str(exc)), False
        except Exception as exc:  # pragma: no cover - defensive conversion
            logger.warning("Remote %s unexpected error: %s", remote_name, exc, exc_info=True)
            return remote_entry_error("api_error", f"Unexpected remote error: {exc}"), False

    if len(remotes) > 1:
        with ThreadPoolExecutor(max_workers=len(remotes)) as executor:
            futures = {
                remote_name: executor.submit(run_remote_operation, remote_name)
                for remote_name in remotes
            }
            for remote_name in remotes:
                remote_entry, ok = futures[remote_name].result()
                remote_results[remote_name] = remote_entry
                if ok:
                    succeeded.append(remote_name)
                else:
                    failed.append(remote_name)
    else:
        for remote_name in remotes:
            remote_entry, ok = run_remote_operation(remote_name)
            remote_results[remote_name] = remote_entry
            if ok:
                succeeded.append(remote_name)
            else:
                failed.append(remote_name)

    if not succeeded:
        error_codes = [
            remote_results[name]["error"]["code"]
            for name in failed
            if remote_results.get(name, {}).get("error")
        ]
        messages = [
            f"{name}: {remote_results[name]['error']['message']}"
            for name in failed
            if remote_results.get(name, {}).get("error")
        ]
        combined = "; ".join(messages) if messages else "All remote requests failed."

        if error_codes and all(code == "auth_failure" for code in error_codes):
            raise SmithAuthError(combined)
        if len(remotes) == 1 and any(code == "invalid_args" for code in error_codes):
            raise ValueError(combined)
        raise SmithApiError(combined)

    return {
        "remotes": remote_results,
        "summary": {
            "requested_remote": requested_remote,
            "queried": remotes,
            "succeeded": succeeded,
            "failed": failed,
        },
    }
