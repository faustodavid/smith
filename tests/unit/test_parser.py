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


def test_azdo_org_flag_parses() -> None:
    parser = build_parser()
    args = parser.parse_args(["--azdo-org", "my-azdo", "projects", "list", "azdo"])

    assert args.azdo_org == "my-azdo"
    assert args.github_org is None


def test_github_org_flag_parses() -> None:
    parser = build_parser()
    args = parser.parse_args(["--github-org", "my-gh", "repos", "list", "github"])

    assert args.github_org == "my-gh"
    assert args.azdo_org is None


def test_both_org_flags_parse_independently() -> None:
    parser = build_parser()
    args = parser.parse_args(["--azdo-org", "a-org", "--github-org", "g-org", "code", "search", "test"])

    assert args.azdo_org == "a-org"
    assert args.github_org == "g-org"
