from __future__ import annotations

import os
import shutil
import sys
import textwrap


def _ansi_enabled() -> bool:
    if os.getenv("NO_COLOR"):
        return False
    if os.getenv("TERM", "").lower() == "dumb":
        return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def _style(value: str, code: str) -> str:
    if not _ansi_enabled():
        return value
    return f"\033[{code}m{value}\033[0m"


def _accent(value: str) -> str:
    return _style(value, "36;1")


def _success(value: str) -> str:
    return _style(value, "32;1")


def _warning(value: str) -> str:
    return _style(value, "33;1")


def _dim(value: str) -> str:
    return _style(value, "2")


def _status(label: str) -> str:
    raw = f"[{label}]"
    if label in {"OK", "READY", "SAVED", "SAFE", "LINKED"}:
        return _success(raw)
    if label in {"MISSING", "NEEDS"}:
        return _warning(raw)
    return _accent(raw)


def _terminal_width() -> int:
    return max(72, min(shutil.get_terminal_size((88, 24)).columns, 110))


def _print_rule(title: str) -> None:
    if not _ansi_enabled():
        print()
        print(title.replace("//", "-"))
        return
    width = _terminal_width()
    label = f"== {title} "
    line = (label + ("=" * width))[:width]
    print(_accent(line))


def _print_wrapped(text: str, *, indent: str = "  ") -> None:
    for line in textwrap.wrap(text, width=_terminal_width() - len(indent), break_long_words=False, break_on_hyphens=False):
        print(f"{indent}{line}")


def _print_panel(title: str, lines: list[str]) -> None:
    if not _ansi_enabled():
        print(title)
        for line in lines:
            if line:
                _print_wrapped(line)
            else:
                print()
        return

    width = _terminal_width()
    border = "+" + ("-" * (width - 2)) + "+"
    print(_accent(border))
    print(_accent(f"| {title:<{width - 4}} |"))
    print(_accent(border))
    for line in lines:
        if not line:
            print(f"| {'':<{width - 4}} |")
            continue
        for wrapped in textwrap.wrap(line, width=width - 6, break_long_words=True, break_on_hyphens=False):
            print(f"|  {wrapped:<{width - 6}}  |")
    print(_accent(border))
