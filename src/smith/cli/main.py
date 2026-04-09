#!/usr/bin/env python3
from __future__ import annotations

import logging
import sys

from smith.cli.handlers import (
    EXIT_API_FAILURE,
    EXIT_AUTH_FAILURE,
    EXIT_INVALID_ARGS,
    _client_from_args,
    _emit_error,
    validate_args_for_provider,
)
from smith.cli.parser import build_parser
from smith.errors import SmithApiError, SmithAuthError


def _configure_logging(*, verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("smith")
    root.setLevel(level)
    root.addHandler(handler)


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]

    parser = build_parser()
    try:
        args = parser.parse_args(raw_argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0

    _configure_logging(verbose=getattr(args, "verbose", False))

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return EXIT_INVALID_ARGS

    command = getattr(args, "command_id", "unknown")

    try:
        validate_args_for_provider(args)
        requires_client = bool(getattr(args, "requires_client", True))
        client = _client_from_args(args) if requires_client else None
        return handler(client, args)
    except ValueError as exc:
        return _emit_error(
            args=args,
            command=command,
            code="invalid_args",
            message=str(exc),
            exit_code=EXIT_INVALID_ARGS,
        )
    except SmithAuthError as exc:
        return _emit_error(
            args=args,
            command=command,
            code="auth_failure",
            message=str(exc),
            exit_code=EXIT_AUTH_FAILURE,
        )
    except SmithApiError as exc:
        return _emit_error(
            args=args,
            command=command,
            code="api_error",
            message=str(exc),
            exit_code=EXIT_API_FAILURE,
        )
    except Exception as exc:  # pragma: no cover - defensive fallback
        return _emit_error(
            args=args,
            command=command,
            code="unexpected_error",
            message=f"Unexpected error: {exc}",
            exit_code=EXIT_API_FAILURE,
        )


def entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
