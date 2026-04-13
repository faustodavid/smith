from __future__ import annotations

from typing import Any
from unittest.mock import Mock, call

import pytest

from smith.errors import SmithApiError, SmithAuthError
from smith.fanout import run_fanout


def _remote_entry_success(payload: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "data": payload,
        "warnings": [],
        "partial": False,
        "error": None,
    }


def _remote_entry_error(code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "data": None,
        "warnings": [],
        "partial": False,
        "error": {"code": code, "message": message},
    }


def _entry_builders() -> tuple[Mock, Mock]:
    success = Mock(side_effect=_remote_entry_success)
    error = Mock(side_effect=_remote_entry_error)
    return success, error


def test_run_fanout_single_remote_success_returns_expected_shape() -> None:
    success_entry, error_entry = _entry_builders()
    operation = Mock(return_value={"items": [1, 2]})

    result = run_fanout(
        remotes=["azdo"],
        requested_remote="azdo",
        operations={"azdo": operation},
        remote_entry_success=success_entry,
        remote_entry_error=error_entry,
    )

    assert result["summary"]["requested_remote"] == "azdo"
    assert result["summary"]["succeeded"] == ["azdo"]
    assert result["summary"]["failed"] == []
    assert result["remotes"]["azdo"]["ok"] is True
    assert result["remotes"]["azdo"]["data"] == {"items": [1, 2]}
    operation.assert_called_once_with()
    success_entry.assert_called_once_with({"items": [1, 2]})
    error_entry.assert_not_called()


def test_run_fanout_single_remote_auth_failure_raises_smith_auth_error() -> None:
    success_entry, error_entry = _entry_builders()
    operation = Mock(side_effect=SmithAuthError("token expired"))

    with pytest.raises(SmithAuthError, match="azdo: token expired"):
        run_fanout(
            remotes=["azdo"],
            requested_remote="azdo",
            operations={"azdo": operation},
            remote_entry_success=success_entry,
            remote_entry_error=error_entry,
        )

    error_entry.assert_called_once_with("auth_failure", "token expired")
    success_entry.assert_not_called()


def test_run_fanout_single_remote_api_failure_raises_smith_api_error() -> None:
    success_entry, error_entry = _entry_builders()
    operation = Mock(side_effect=SmithApiError("upstream failed"))

    with pytest.raises(SmithApiError, match="azdo: upstream failed"):
        run_fanout(
            remotes=["azdo"],
            requested_remote="azdo",
            operations={"azdo": operation},
            remote_entry_success=success_entry,
            remote_entry_error=error_entry,
        )

    error_entry.assert_called_once_with("api_error", "upstream failed")
    success_entry.assert_not_called()


def test_run_fanout_single_remote_value_error_reraises_value_error() -> None:
    success_entry, error_entry = _entry_builders()
    operation = Mock(side_effect=ValueError("missing argument"))

    with pytest.raises(ValueError, match="azdo: missing argument"):
        run_fanout(
            remotes=["azdo"],
            requested_remote="azdo",
            operations={"azdo": operation},
            remote_entry_success=success_entry,
            remote_entry_error=error_entry,
        )

    error_entry.assert_called_once_with("invalid_args", "missing argument")
    success_entry.assert_not_called()


def test_run_fanout_multi_remote_both_succeed() -> None:
    success_entry, error_entry = _entry_builders()
    azdo_operation = Mock(return_value={"provider": "azdo"})
    github_operation = Mock(return_value={"provider": "github"})

    result = run_fanout(
        remotes=["azdo", "github"],
        requested_remote="all",
        operations={"azdo": azdo_operation, "github": github_operation},
        remote_entry_success=success_entry,
        remote_entry_error=error_entry,
    )

    assert result["summary"]["requested_remote"] == "all"
    assert result["summary"]["queried"] == ["azdo", "github"]
    assert result["summary"]["succeeded"] == ["azdo", "github"]
    assert result["summary"]["failed"] == []
    assert result["remotes"]["azdo"]["ok"] is True
    assert result["remotes"]["github"]["ok"] is True
    assert success_entry.call_count == 2
    error_entry.assert_not_called()


def test_run_fanout_multi_remote_partial_success_returns_partial_result() -> None:
    success_entry, error_entry = _entry_builders()
    azdo_operation = Mock(return_value={"provider": "azdo"})
    github_operation = Mock(side_effect=SmithApiError("rate limited"))

    result = run_fanout(
        remotes=["azdo", "github"],
        requested_remote="all",
        operations={"azdo": azdo_operation, "github": github_operation},
        remote_entry_success=success_entry,
        remote_entry_error=error_entry,
    )

    assert result["summary"]["succeeded"] == ["azdo"]
    assert result["summary"]["failed"] == ["github"]
    assert result["remotes"]["azdo"]["ok"] is True
    assert result["remotes"]["github"]["ok"] is False
    assert result["remotes"]["github"]["error"]["code"] == "api_error"
    assert result["remotes"]["github"]["error"]["message"] == "rate limited"


def test_run_fanout_all_fail_with_auth_errors_raises_smith_auth_error() -> None:
    success_entry, error_entry = _entry_builders()
    azdo_operation = Mock(side_effect=SmithAuthError("azdo auth failed"))
    github_operation = Mock(side_effect=SmithAuthError("github auth failed"))

    with pytest.raises(SmithAuthError) as exc_info:
        run_fanout(
            remotes=["azdo", "github"],
            requested_remote="all",
            operations={"azdo": azdo_operation, "github": github_operation},
            remote_entry_success=success_entry,
            remote_entry_error=error_entry,
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
            remotes=["azdo", "github"],
            requested_remote="all",
            operations={"azdo": azdo_operation, "github": github_operation},
            remote_entry_success=success_entry,
            remote_entry_error=error_entry,
        )

    message = str(exc_info.value)
    assert "azdo: azdo auth failed" in message
    assert "github: github api failed" in message


def test_run_fanout_unsupported_remote_name_is_reported_in_result() -> None:
    success_entry, error_entry = _entry_builders()
    azdo_operation = Mock(return_value={"provider": "azdo"})

    result = run_fanout(
        remotes=["azdo", "unknown"],
        requested_remote="all",
        operations={"azdo": azdo_operation},
        remote_entry_success=success_entry,
        remote_entry_error=error_entry,
    )

    assert result["summary"]["succeeded"] == ["azdo"]
    assert result["summary"]["failed"] == ["unknown"]
    assert result["remotes"]["unknown"]["ok"] is False
    assert result["remotes"]["unknown"]["error"]["code"] == "unsupported_remote"
    assert "Remote 'unknown' is not supported" in result["remotes"]["unknown"]["error"]["message"]
    assert error_entry.call_args_list[-1] == call(
        "unsupported_remote",
        "Remote 'unknown' is not supported for this command.",
    )
