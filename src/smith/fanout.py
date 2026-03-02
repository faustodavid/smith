from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from smith.errors import SmithApiError, SmithAuthError


def run_fanout(
    *,
    providers: list[str],
    requested_provider: str,
    operations: dict[str, Callable[[], Any]],
    provider_entry_success: Callable[[Any], dict[str, Any]],
    provider_entry_error: Callable[[str, str], dict[str, Any]],
) -> dict[str, Any]:
    provider_results: dict[str, dict[str, Any]] = {}
    succeeded: list[str] = []
    failed: list[str] = []

    def run_provider_operation(provider_name: str) -> tuple[dict[str, Any], bool]:
        operation = operations.get(provider_name)
        if operation is None:
            return (
                provider_entry_error(
                    "unsupported_provider",
                    f"Provider '{provider_name}' is not supported for this command.",
                ),
                False,
            )

        try:
            payload = operation()
            return provider_entry_success(payload), True
        except ValueError as exc:
            return provider_entry_error("invalid_args", str(exc)), False
        except SmithAuthError as exc:
            return provider_entry_error("auth_failure", str(exc)), False
        except SmithApiError as exc:
            return provider_entry_error("api_error", str(exc)), False
        except Exception as exc:  # pragma: no cover - defensive conversion
            return provider_entry_error("api_error", f"Unexpected provider error: {exc}"), False

    if len(providers) > 1:
        with ThreadPoolExecutor(max_workers=len(providers)) as executor:
            futures = {
                provider_name: executor.submit(run_provider_operation, provider_name)
                for provider_name in providers
            }
            for provider_name in providers:
                provider_entry, ok = futures[provider_name].result()
                provider_results[provider_name] = provider_entry
                if ok:
                    succeeded.append(provider_name)
                else:
                    failed.append(provider_name)
    else:
        for provider_name in providers:
            provider_entry, ok = run_provider_operation(provider_name)
            provider_results[provider_name] = provider_entry
            if ok:
                succeeded.append(provider_name)
            else:
                failed.append(provider_name)

    if not succeeded:
        error_codes = [
            provider_results[name]["error"]["code"]
            for name in failed
            if provider_results.get(name, {}).get("error")
        ]
        messages = [
            f"{name}: {provider_results[name]['error']['message']}"
            for name in failed
            if provider_results.get(name, {}).get("error")
        ]
        combined = "; ".join(messages) if messages else "All provider requests failed."

        if error_codes and all(code == "auth_failure" for code in error_codes):
            raise SmithAuthError(combined)
        if len(providers) == 1 and any(code == "invalid_args" for code in error_codes):
            raise ValueError(combined)
        raise SmithApiError(combined)

    return {
        "providers": provider_results,
        "summary": {
            "requested_provider": requested_provider,
            "queried": providers,
            "succeeded": succeeded,
            "failed": failed,
        },
    }
