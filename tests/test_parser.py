from __future__ import annotations

from smith.cli.parser import build_parser


def test_code_search_parser_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(["code", "search", "grafana"])

    assert args.command_id == "code.search"
    assert args.provider == "all"
    assert args.output_format == "text"
    assert args.query == "grafana"


def test_board_alias_group_stories_parses() -> None:
    parser = build_parser()
    args = parser.parse_args(["stories", "ticket", "azdo", "SRE", "123"])

    assert args.command_id == "board.ticket"
    assert args.provider == "azdo"
    assert args.project == "SRE"
    assert args.id == 123
