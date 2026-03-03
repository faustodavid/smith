from __future__ import annotations

from typing import Any
from unittest.mock import Mock, call

import pytest

from smith.errors import SmithApiError, SmithAuthError
from smith.fanout import run_fanout


def _provider_entry_success(payload: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "data": payload,
        "warnings": [],
        "partial": False,
        "error": None,
    }


def _provider_entry_error(code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "data": None,
        "warnings": [],
        "partial": False,
        "error": {"code": code, "message": message},
    }


def _entry_builders() -> tuple[Mock, Mock]:
    success = Mock(side_effect=_provider_entry_success)
    error = Mock(side_effect=_provider_entry_error)
    return success, error


def test_run_fanout_single_provider_success_returns_expected_shape() -> None:
    success_entry, error_entry = _entry_builders()
    operation = Mock(return_value={"items": [1, 2]})

    result = run_fanout(
        providers=["azdo"],
        requested_provider="azdo",
        operations={"azdo": operation},
        provider_entry_success=success_entry,
        provider_entry_error=error_entry,
    )

    assert result["summary"]["requested_provider"] == "azdo"
    assert result["summary"]["succeeded"] == ["azdo"]
    assert result["summary"]["failed"] == []
    assert result["providers"]["azdo"]["ok"] is True
    assert result["providers"]["azdo"]["data"] == {"items": [1, 2]}
    operation.assert_called_once_with()
    success_entry.assert_called_once_with({"items": [1, 2]})
    error_entry.assert_not_called()


def test_run_fanout_single_provider_auth_failure_raises_smith_auth_error() -> None:
    success_entry, error_entry = _entry_builders()
    operation = Mock(side_effect=SmithAuthError("token expired"))

    with pytest.raises(SmithAuthError, match="azdo: token expired"):
        run_fanout(
            providers=["azdo"],
            requested_provider="azdo",
            operations={"azdo": operation},
            provider_entry_success=success_entry,
            provider_entry_error=error_entry,
        )

    error_entry.assert_called_once_with("auth_failure", "token expired")
    success_entry.assert_not_called()


def test_run_fanout_single_provider_api_failure_raises_smith_api_error() -> None:
    success_entry, error_entry = _entry_builders()
    operation = Mock(side_effect=SmithApiError("upstream failed"))

    with pytest.raises(SmithApiError, match="azdo: upstream failed"):
        run_fanout(
            providers=["azdo"],
            requested_provider="azdo",
            operations={"azdo": operation},
            provider_entry_success=success_entry,
            provider_entry_error=error_entry,
        )

    error_entry.assert_called_once_with("api_error", "upstream failed")
    success_entry.assert_not_called()


def test_run_fanout_single_provider_value_error_reraises_value_error() -> None:
    success_entry, error_entry = _entry_builders()
    operation = Mock(side_effect=ValueError("missing argument"))

    with pytest.raises(ValueError, match="azdo: missing argument"):
        run_fanout(
            providers=["azdo"],
            requested_provider="azdo",
            operations={"azdo": operation},
            provider_entry_success=success_entry,
            provider_entry_error=error_entry,
        )

    error_entry.assert_called_once_with("invalid_args", "missing argument")
    success_entry.assert_not_called()


def test_run_fanout_multi_provider_both_succeed() -> None:
    success_entry, error_entry = _entry_builders()
    azdo_operation = Mock(return_value={"provider": "azdo"})
    github_operation = Mock(return_value={"provider": "github"})

    result = run_fanout(
        providers=["azdo", "github"],
        requested_provider="all",
        operations={"azdo": azdo_operation, "github": github_operation},
        provider_entry_success=success_entry,
        provider_entry_error=error_entry,
    )

    assert result["summary"]["requested_provider"] == "all"
    assert result["summary"]["queried"] == ["azdo", "github"]
    assert result["summary"]["succeeded"] == ["azdo", "github"]
    assert result["summary"]["failed"] == []
    assert result["providers"]["azdo"]["ok"] is True
    assert result["providers"]["github"]["ok"] is True
    assert success_entry.call_count == 2
    error_entry.assert_not_called()


def test_run_fanout_multi_provider_partial_success_returns_partial_result() -> None:
    success_entry, error_entry = _entry_builders()
    azdo_operation = Mock(return_value={"provider": "azdo"})
    github_operation = Mock(side_effect=SmithApiError("rate limited"))

    result = run_fanout(
        providers=["azdo", "github"],
        requested_provider="all",
        operations={"azdo": azdo_operation, "github": github_operation},
        provider_entry_success=success_entry,
        provider_entry_error=error_entry,
    )

    assert result["summary"]["succeeded"] == ["azdo"]
    assert result["summary"]["failed"] == ["github"]
    assert result["providers"]["azdo"]["ok"] is True
    assert result["providers"]["github"]["ok"] is False
    assert result["providers"]["github"]["error"]["code"] == "api_error"
    assert result["providers"]["github"]["error"]["message"] == "rate limited"


def test_run_fanout_all_fail_with_auth_errors_raises_smith_auth_error() -> None:
    success_entry, error_entry = _entry_builders()
    azdo_operation = Mock(side_effect=SmithAuthError("azdo auth failed"))
    github_operation = Mock(side_effect=SmithAuthError("github auth failed"))

    with pytest.raises(SmithAuthError) as exc_info:
        run_fanout(
            providers=["azdo", "github"],
            requested_provider="all",
            operations={"azdo": azdo_operation, "github": github_operation},
            provider_entry_success=success_entry,
            provider_entry_error=error_entry,
        )

    message = str(exc_info.value)
    assert "azdo: azdo auth failed" in message
    assert "github: github auth failed" in message


def test_run_fanout_all_fail_with_mixed_errors_raises_smith_api_error() -> None:
    success_entry, error_entry = _entry_builders()
    azdo_operation = Mock(side_effect=SmithAuthError("azdo auth failed"))
    github_operation = Mock(side_effect=SmithApiError("github api failed"))

    with pytest.raises(SmithApiError) as exc_info:
        run_fanout(
            providers=["azdo", "github"],
            requested_provider="all",
            operations={"azdo": azdo_operation, "github": github_operation},
            provider_entry_success=success_entry,
            provider_entry_error=error_entry,
        )

    message = str(exc_info.value)
    assert "azdo: azdo auth failed" in message
    assert "github: github api failed" in message


def test_run_fanout_unsupported_provider_name_is_reported_in_result() -> None:
    success_entry, error_entry = _entry_builders()
    azdo_operation = Mock(return_value={"provider": "azdo"})

    result = run_fanout(
        providers=["azdo", "unknown"],
        requested_provider="all",
        operations={"azdo": azdo_operation},
        provider_entry_success=success_entry,
        provider_entry_error=error_entry,
    )

    assert result["summary"]["succeeded"] == ["azdo"]
    assert result["summary"]["failed"] == ["unknown"]
    assert result["providers"]["unknown"]["ok"] is False
    assert result["providers"]["unknown"]["error"]["code"] == "unsupported_provider"
    assert "Provider 'unknown' is not supported" in result["providers"]["unknown"]["error"]["message"]
    assert error_entry.call_args_list[-1] == call(
        "unsupported_provider",
        "Provider 'unknown' is not supported for this command.",
    )
