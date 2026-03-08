from __future__ import annotations

import pytest

from smith.cli.parser import build_parser


def test_code_search_parser_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(["code", "search", "grafana"])

    assert args.command_id == "code.search"
    assert args.provider == "all"
    assert args.output_format == "text"
    assert args.query == "grafana"


def test_stories_group_parses_to_stories_command() -> None:
    parser = build_parser()
    args = parser.parse_args(["stories", "get", "azdo", "SRE", "123"])

    assert args.command_id == "stories.get"
    assert args.provider == "azdo"
    assert args.project == "SRE"
    assert args.id == 123


def test_organizations_parser_uses_canonical_command_id() -> None:
    parser = build_parser()
    args = parser.parse_args(["organizations", "azdo"])

    assert args.command_id == "organizations"
    assert args.provider == "azdo"


def test_repos_parser_uses_canonical_command_id() -> None:
    parser = build_parser()
    args = parser.parse_args(["repos", "github"])

    assert args.command_id == "repos"
    assert args.provider == "github"
    assert args.project is None


def test_ci_logs_list_parser_uses_canonical_command_id() -> None:
    parser = build_parser()
    args = parser.parse_args(["ci", "logs", "list", "azdo", "SRE", "42"])

    assert args.command_id == "ci.logs.list"
    assert args.provider == "azdo"
    assert args.project == "SRE"
    assert args.id == 42


def test_ci_logs_grep_parser_uses_canonical_command_id() -> None:
    parser = build_parser()
    args = parser.parse_args(["ci", "logs", "grep", "github", "repo-a", "42", "--pattern", "error"])

    assert args.command_id == "ci.logs.grep"
    assert args.provider == "github"
    assert args.repo == "repo-a"
    assert args.id == 42
    assert args.pattern == "error"


@pytest.mark.parametrize(
    "argv",
    [
        ["discover", "projects", "azdo"],
        ["work", "get", "azdo", "SRE", "123"],
        ["ci", "grep", "azdo", "SRE", "42"],
        ["ci", "logs", "azdo", "SRE", "42"],
        ["stories", "ticket", "azdo", "SRE", "123"],
    ],
)
def test_legacy_paths_fail_to_parse(argv: list[str]) -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(argv)


def test_azdo_org_flag_parses() -> None:
    parser = build_parser()
    args = parser.parse_args(["--azdo-org", "my-azdo", "organizations", "azdo"])

    assert args.azdo_org == "my-azdo"
    assert args.github_org is None


def test_github_org_flag_parses() -> None:
    parser = build_parser()
    args = parser.parse_args(["--github-org", "my-gh", "repos", "github"])

    assert args.github_org == "my-gh"
    assert args.azdo_org is None


def test_both_org_flags_parse_independently() -> None:
    parser = build_parser()
    args = parser.parse_args(["--azdo-org", "a-org", "--github-org", "g-org", "code", "search", "test"])

    assert args.azdo_org == "a-org"
    assert args.github_org == "g-org"


def test_root_help_lists_new_command_tree(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])

    output = capsys.readouterr().out
    assert "repos" in output
    assert "organizations" in output
    assert "stories" in output
    assert "discover" not in output
    assert "\n    work" not in output
    assert "List repositories" in output
    assert "List GitHub organization or Azure DevOps projects" in output
    assert "Search and grep across providers and repos" in output
    assert "List, get, and read comments" in output
    assert "Read and grep logs" in output
    assert "Get, search, and get mine" in output


def test_stories_help_lists_get_search_and_mine_only(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["stories", "--help"])

    output = capsys.readouterr().out
    assert "get" in output
    assert "search" in output
    assert "mine" in output
    assert "query" not in output


def test_ci_help_lists_only_logs(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["ci", "--help"])

    output = capsys.readouterr().out
    assert "logs" in output
    assert "Inspect CI logs" in output
    assert "{logs}" in output
    assert "\n    grep" not in output


def test_ci_logs_help_lists_list_and_grep(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["ci", "logs", "--help"])

    output = capsys.readouterr().out
    assert "list" in output
    assert "grep" in output
    assert "List logs for a CI run" in output
    assert "Search or read CI logs" in output


def test_stories_query_path_fails_to_parse() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["stories", "query", "azdo", "SRE", "--wiql", "SELECT 1"])
