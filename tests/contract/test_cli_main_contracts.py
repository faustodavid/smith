from __future__ import annotations

from argparse import Namespace
from typing import Any

from smith.cli import main as cli_main


class _Parser:
    def __init__(self, *, args: Namespace | None = None, parse_error: int | None = None) -> None:
        self.args = args
        self.parse_error = parse_error
        self.help_called = False

    def parse_args(self, argv: list[str]) -> Namespace:
        if self.parse_error is not None:
            raise SystemExit(self.parse_error)
        if self.args is None:
            raise AssertionError("args must be provided when parse_error is not set")
        return self.args

    def print_help(self) -> None:
        self.help_called = True


def test_configure_logging_sets_expected_level() -> None:
    cli_main._configure_logging(verbose=True)
    assert cli_main.logging.getLogger("smith").level == cli_main.logging.DEBUG


def test_main_returns_parse_exit_code(monkeypatch: Any) -> None:
    parser = _Parser(parse_error=2)
    monkeypatch.setattr(cli_main, "build_parser", lambda: parser)

    assert cli_main.main(["--bad-flag"]) == 2


def test_main_prints_help_when_no_handler(monkeypatch: Any) -> None:
    parser = _Parser(args=Namespace(verbose=False, handler=None, output_format="text"))
    monkeypatch.setattr(cli_main, "build_parser", lambda: parser)

    assert cli_main.main(["orgs"]) == cli_main.EXIT_INVALID_ARGS
    assert parser.help_called is True


def test_main_emits_json_for_value_errors(monkeypatch: Any, capsys: Any) -> None:
    args = Namespace(verbose=False, handler=lambda client, parsed: 0, output_format="json", command_id="repos")
    parser = _Parser(args=args)
    monkeypatch.setattr(cli_main, "build_parser", lambda: parser)
    monkeypatch.setattr(cli_main, "validate_args_for_remote", lambda parsed: (_ for _ in ()).throw(ValueError("bad args")))

    assert cli_main.main(["github", "repos"]) == cli_main.EXIT_INVALID_ARGS
    captured = capsys.readouterr()
    assert '"code": "invalid_args"' in captured.out
    assert '"message": "bad args"' in captured.out


def test_main_uses_unexpected_error_fallback(monkeypatch: Any, capsys: Any) -> None:
    args = Namespace(
        verbose=False,
        handler=lambda client, parsed: (_ for _ in ()).throw(RuntimeError("boom")),
        output_format="text",
        command_id="repos",
    )
    parser = _Parser(args=args)
    monkeypatch.setattr(cli_main, "build_parser", lambda: parser)
    monkeypatch.setattr(cli_main, "validate_args_for_remote", lambda parsed: None)
    monkeypatch.setattr(cli_main, "_client_from_args", lambda parsed: object())

    assert cli_main.main(["github", "repos"]) == cli_main.EXIT_API_FAILURE
    captured = capsys.readouterr()
    assert "Unexpected error: boom" in captured.err


def test_main_skips_client_creation_for_clientless_handlers(monkeypatch: Any) -> None:
    received: dict[str, Any] = {}

    def _handler(client: Any, parsed: Namespace) -> int:
        del parsed
        received["client"] = client
        return 0

    args = Namespace(
        verbose=False,
        handler=_handler,
        output_format="text",
        command_id="cache.clean",
        requires_client=False,
    )
    parser = _Parser(args=args)
    monkeypatch.setattr(cli_main, "build_parser", lambda: parser)
    monkeypatch.setattr(cli_main, "validate_args_for_remote", lambda parsed: None)
    monkeypatch.setattr(
        cli_main,
        "_client_from_args",
        lambda parsed: (_ for _ in ()).throw(AssertionError("client should not be created")),
    )

    assert cli_main.main(["cache", "clean"]) == 0
    assert received == {"client": None}


def test_entrypoint_raises_system_exit(monkeypatch: Any) -> None:
    monkeypatch.setattr(cli_main, "main", lambda argv=None: 7)

    try:
        cli_main.entrypoint()
    except SystemExit as exc:
        assert exc.code == 7
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("entrypoint() did not raise SystemExit")
