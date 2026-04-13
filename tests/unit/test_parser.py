from __future__ import annotations

import pytest

from smith.cli.parser import build_parser
from smith.config import RemoteConfig, SmithConfig
from smith.discovery import DEFAULT_DISCOVERY_TAKE


def _build_test_parser() -> object:
    return build_parser(
        smith_config=SmithConfig(
            remotes={
                "azdo": RemoteConfig(
                    name="azdo",
                    provider="azdo",
                    org="acme",
                    host="dev.azure.com",
                    token_env="AZURE_DEVOPS_PAT",
                    enabled=True,
                    api_url="https://dev.azure.com",
                ),
                "github": RemoteConfig(
                    name="github",
                    provider="github",
                    org="octo-org",
                    host="github.com",
                    token_env="GITHUB_TOKEN",
                    enabled=True,
                    api_url="https://api.github.com",
                ),
                "gitlab": RemoteConfig(
                    name="gitlab",
                    provider="gitlab",
                    org="",
                    host="gitlab.com",
                    token_env="GITLAB_TOKEN",
                    enabled=True,
                    api_url="https://gitlab.com/api/v4",
                ),
                "gitlab-infra": RemoteConfig(
                    name="gitlab-infra",
                    provider="gitlab",
                    org="",
                    host="gitlab-infra.example.com",
                    token_env="GITLAB_INFRA_TOKEN",
                    enabled=True,
                    api_url="https://gitlab-infra.example.com/api/v4",
                ),
                "youtrack": RemoteConfig(
                    name="youtrack",
                    provider="youtrack",
                    org="",
                    host="youtrack.example.com",
                    token_env="YOUTRACK_TOKEN",
                    enabled=True,
                    api_url="https://youtrack.example.com/api",
                ),
            },
            defaults={},
        )
    )


def test_code_search_parser_defaults() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["code", "search", "grafana"])

    assert args.command_id == "code.search"
    assert args.remote == "all"
    assert args.output_format == "text"
    assert args.project is None
    assert args.query == "grafana"
    assert args.repos is None


def test_cache_clean_parser_defaults() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["cache", "clean"])

    assert args.command_id == "cache.clean"
    assert args.cache_remote == "all"
    assert args.requires_client is False


def test_cache_clean_parser_accepts_remote_override() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["cache", "clean", "--remote", "github"])

    assert args.command_id == "cache.clean"
    assert args.cache_remote == "github"


def test_stories_group_parses_to_stories_command() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["azdo", "stories", "get", "SRE", "123"])

    assert args.command_id == "stories.get"
    assert args.remote == "azdo"
    assert args.remote_provider == "azdo"
    assert args.project == "SRE"
    assert args.id == 123


def test_youtrack_stories_get_parser_accepts_string_id() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["youtrack", "stories", "get", "RAD-1055"])

    assert args.command_id == "stories.get"
    assert args.remote == "youtrack"
    assert args.remote_provider == "youtrack"
    assert args.project is None
    assert args.repo is None
    assert args.id == "RAD-1055"


def test_youtrack_stories_get_parser_rejects_removed_no_images_flag() -> None:
    parser = _build_test_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["youtrack", "stories", "get", "RAD-1055", "--no-images"])


def test_youtrack_stories_search_and_mine_omit_repo_and_project() -> None:
    parser = _build_test_parser()
    search_args = parser.parse_args(["youtrack", "stories", "search", "--query", "patching flow"])
    mine_args = parser.parse_args(["youtrack", "stories", "mine", "--include-closed"])

    assert search_args.command_id == "stories.search"
    assert search_args.project is None
    assert search_args.repo is None
    assert search_args.area is None
    assert search_args.query == "patching flow"

    assert mine_args.command_id == "stories.mine"
    assert mine_args.project is None
    assert mine_args.repo is None
    assert mine_args.include_closed is True


def test_youtrack_stories_search_rejects_hidden_area_flag() -> None:
    parser = _build_test_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["youtrack", "stories", "search", "--query", "patching flow", "--area", "Ops"])


@pytest.mark.parametrize("provider", ["azdo", "github"])
def test_orgs_parser_uses_canonical_command_id(provider: str) -> None:
    parser = _build_test_parser()
    args = parser.parse_args([provider, "orgs"])

    assert args.command_id == "orgs"
    assert args.remote == provider
    assert args.remote_provider == provider


def test_gitlab_orgs_path_fails_to_parse() -> None:
    parser = _build_test_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["gitlab", "orgs"])


def test_repos_parser_uses_canonical_command_id() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["github", "repos"])

    assert args.command_id == "repos"
    assert args.remote == "github"
    assert args.remote_provider == "github"
    assert args.project is None


def test_repos_gitlab_parser_uses_canonical_command_id() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["gitlab", "repos"])

    assert args.command_id == "repos"
    assert args.remote == "gitlab"
    assert args.remote_provider == "gitlab"
    assert args.project is None
    assert args.group is None
    assert args.grep is None
    assert args.skip == 0
    assert args.take == DEFAULT_DISCOVERY_TAKE


def test_repos_parser_accepts_named_remote() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["gitlab-infra", "repos"])

    assert args.command_id == "repos"
    assert args.remote == "gitlab-infra"
    assert args.remote_provider == "gitlab"
    assert args.project is None


def test_gitlab_repos_parser_accepts_group_filter_and_discovery_flags() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(
        [
            "gitlab-infra",
            "repos",
            "engineering-tools",
            "--grep",
            "^engineering-tools/(api|web)$",
            "--skip",
            "10",
            "--take",
            "25",
        ]
    )

    assert args.command_id == "repos"
    assert args.remote == "gitlab-infra"
    assert args.group == "engineering-tools"
    assert args.grep == "^engineering-tools/(api|web)$"
    assert args.skip == 10
    assert args.take == 25


def test_gitlab_groups_parser_uses_canonical_command_id() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["gitlab-infra", "groups"])

    assert args.command_id == "groups"
    assert args.remote == "gitlab-infra"
    assert args.remote_provider == "gitlab"
    assert args.grep is None
    assert args.skip == 0
    assert args.take == DEFAULT_DISCOVERY_TAKE


def test_gitlab_groups_parser_accepts_discovery_flags() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(
        ["gitlab-infra", "groups", "--grep", "^platform", "--skip", "5", "--take", "15"]
    )

    assert args.command_id == "groups"
    assert args.remote == "gitlab-infra"
    assert args.grep == "^platform"
    assert args.skip == 5
    assert args.take == 15


@pytest.mark.parametrize(
    "argv",
    [
        ["github", "repos", "--grep", "^platform"],
        ["github", "repos", "--take", "10"],
        ["azdo", "repos", "SRE", "--skip", "5"],
        ["github", "groups"],
        ["azdo", "groups"],
    ],
)
def test_non_gitlab_discovery_flags_and_groups_fail_to_parse(argv: list[str]) -> None:
    parser = _build_test_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["youtrack", "repos"],
        ["youtrack", "orgs"],
        ["youtrack", "code", "search", "patch"],
        ["youtrack", "prs", "list", "repo-a"],
        ["youtrack", "pipelines", "logs", "list", "42"],
    ],
)
def test_youtrack_unsupported_groups_fail_to_parse(argv: list[str]) -> None:
    parser = _build_test_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(argv)


def test_prs_list_parser_uses_canonical_command_id() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["azdo", "prs", "list", "SRE", "repo-a"])

    assert args.command_id == "prs.list"
    assert args.remote == "azdo"
    assert args.remote_provider == "azdo"
    assert args.project == "SRE"
    assert args.repo == "repo-a"


def test_prs_get_parser_uses_canonical_command_id() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["github", "prs", "get", "repo-a", "42"])

    assert args.command_id == "prs.get"
    assert args.remote == "github"
    assert args.remote_provider == "github"
    assert args.repo == "repo-a"
    assert args.id == 42


def test_prs_get_gitlab_parser_uses_canonical_command_id() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["gitlab", "prs", "get", "engineering-tools/repo-a", "42"])

    assert args.command_id == "prs.get"
    assert args.remote == "gitlab"
    assert args.remote_provider == "gitlab"
    assert args.repo == "engineering-tools/repo-a"
    assert args.id == 42


def test_prs_threads_parser_uses_canonical_command_id() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["azdo", "prs", "threads", "SRE", "repo-a", "42"])

    assert args.command_id == "prs.threads"
    assert args.remote == "azdo"
    assert args.remote_provider == "azdo"
    assert args.project == "SRE"
    assert args.repo == "repo-a"
    assert args.id == 42


def test_pipelines_logs_list_parser_uses_canonical_command_id() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["azdo", "pipelines", "logs", "list", "SRE", "42"])

    assert args.command_id == "pipelines.logs.list"
    assert args.remote == "azdo"
    assert args.remote_provider == "azdo"
    assert args.project == "SRE"
    assert args.id == 42


def test_code_grep_parser_uses_required_positional_pattern() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["github", "code", "grep", "repo-a", "--path", "/src", "error"])

    assert args.command_id == "code.grep"
    assert args.remote == "github"
    assert args.remote_provider == "github"
    assert args.repo == "repo-a"
    assert args.path == "/src"
    assert args.pattern == "error"
    assert args.no_clone is False


def test_code_grep_parser_accepts_no_clone() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["github", "code", "grep", "repo-a", "--no-clone", "error"])

    assert args.command_id == "code.grep"
    assert args.remote == "github"
    assert args.remote_provider == "github"
    assert args.repo == "repo-a"
    assert args.pattern == "error"
    assert args.no_clone is True


def test_code_grep_gitlab_parser_uses_required_positional_pattern() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["gitlab", "code", "grep", "engineering-tools/repo-a", "--path", "/src", "error"])

    assert args.command_id == "code.grep"
    assert args.remote == "gitlab"
    assert args.remote_provider == "gitlab"
    assert args.repo == "engineering-tools/repo-a"
    assert args.path == "/src"
    assert args.pattern == "error"
    assert args.no_clone is False


def test_code_grep_parser_accepts_named_remote() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["gitlab-infra", "code", "grep", "engineering-tools/repo-a", "--path", "/src", "error"])

    assert args.command_id == "code.grep"
    assert args.remote == "gitlab-infra"
    assert args.remote_provider == "gitlab"
    assert args.repo == "engineering-tools/repo-a"
    assert args.path == "/src"
    assert args.pattern == "error"


def test_pipelines_logs_grep_parser_uses_canonical_command_id() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["github", "pipelines", "logs", "grep", "repo-a", "42", "error"])

    assert args.command_id == "pipelines.logs.grep"
    assert args.remote == "github"
    assert args.remote_provider == "github"
    assert args.repo == "repo-a"
    assert args.id == 42
    assert args.pattern == "error"


def test_pipelines_logs_grep_gitlab_parser_uses_canonical_command_id() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["gitlab", "pipelines", "logs", "grep", "engineering-tools/repo-a", "42", "error"])

    assert args.command_id == "pipelines.logs.grep"
    assert args.remote == "gitlab"
    assert args.remote_provider == "gitlab"
    assert args.repo == "engineering-tools/repo-a"
    assert args.id == 42
    assert args.pattern == "error"


def test_pipelines_logs_grep_parser_accepts_log_id_before_pattern() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["azdo", "pipelines", "logs", "grep", "SRE", "42", "--log-id", "18", "error"])

    assert args.command_id == "pipelines.logs.grep"
    assert args.remote == "azdo"
    assert args.remote_provider == "azdo"
    assert args.project == "SRE"
    assert args.id == 42
    assert args.log_id == 18
    assert args.pattern == "error"


def test_code_search_parser_rejects_removed_global_filters() -> None:
    parser = _build_test_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["code", "search", "grafana", "--remote", "gitlab-infra"])

    with pytest.raises(SystemExit):
        parser.parse_args(["code", "search", "grafana", "--project", "SRE"])

    with pytest.raises(SystemExit):
        parser.parse_args(["code", "search", "grafana", "--repo", "repo-a"])


def test_remote_code_search_parser_uses_named_remote() -> None:
    parser = _build_test_parser()
    args = parser.parse_args(["gitlab-infra", "code", "search", "grafana", "--repo", "engineering-tools/repo-a"])

    assert args.command_id == "code.search"
    assert args.remote == "gitlab-infra"
    assert args.repos == ["engineering-tools/repo-a"]
    assert args.project is None


@pytest.mark.parametrize(
    "argv",
    [
        ["github", "code", "grep", "repo-a"],
        ["azdo", "pipelines", "logs", "grep", "SRE", "42"],
        ["github", "pipelines", "logs", "grep", "repo-a", "42", "--pattern", "error"],
    ],
)
def test_grep_commands_fail_when_pattern_contract_is_not_met(argv: list[str]) -> None:
    parser = _build_test_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["discover", "projects", "azdo"],
        ["organizations"],
        ["organizations", "azdo"],
        ["repos", "github"],
        ["orgs", "azdo"],
        ["search", "grafana"],
        ["gitlab", "orgs"],
        ["gitlab", "groups", "list"],
        ["cache", "clean", "--provider", "github"],
        ["work", "get", "azdo", "SRE", "123"],
        ["pr", "list", "github", "repo-a"],
        ["pr", "get", "github", "repo-a", "42"],
        ["pr", "threads", "azdo", "SRE", "repo-a", "42"],
        ["ci", "grep", "azdo", "SRE", "42"],
        ["ci", "logs", "list", "azdo", "SRE", "42"],
        ["ci", "logs", "azdo", "SRE", "42"],
    ],
)
def test_legacy_paths_fail_to_parse(argv: list[str]) -> None:
    parser = _build_test_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(argv)


def test_root_help_lists_new_command_tree(capsys: pytest.CaptureFixture[str]) -> None:
    parser = _build_test_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])

    output = capsys.readouterr().out
    assert "code" in output
    assert "azdo" in output
    assert "github" in output
    assert "gitlab" in output
    assert "youtrack" in output
    assert "cache" in output
    assert "config" in output
    assert "discover" not in output
    assert "\n    work" not in output
    assert "organizations" not in output
    assert "Search code across all configured remotes" in output
    assert "Manage local grep caches" in output


def test_remote_help_lists_provider_commands(capsys: pytest.CaptureFixture[str]) -> None:
    parser = _build_test_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["gitlab-infra", "--help"])

    output = capsys.readouterr().out
    assert "repos" in output
    assert "groups" in output
    assert "code" in output
    assert "prs" in output
    assert "pipelines" in output
    assert "stories" in output
    assert "orgs" not in output


def test_youtrack_remote_help_lists_only_stories(capsys: pytest.CaptureFixture[str]) -> None:
    parser = _build_test_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["youtrack", "--help"])

    output = capsys.readouterr().out
    assert "stories" in output
    assert "repos" not in output
    assert "code" not in output
    assert "prs" not in output
    assert "pipelines" not in output
    assert "orgs" not in output


def test_pipelines_help_lists_only_logs(capsys: pytest.CaptureFixture[str]) -> None:
    parser = _build_test_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["gitlab", "pipelines", "--help"])

    output = capsys.readouterr().out
    assert "logs" in output
    assert "Inspect pipeline logs" in output
    assert "{logs}" in output
    assert "\n    grep" not in output


def test_pipelines_logs_help_lists_list_and_grep(capsys: pytest.CaptureFixture[str]) -> None:
    parser = _build_test_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["gitlab", "pipelines", "logs", "--help"])

    output = capsys.readouterr().out
    assert "list" in output
    assert "grep" in output
    assert "List logs for a pipeline run" in output
    assert "Search or read pipeline logs" in output


def test_stories_query_path_fails_to_parse() -> None:
    parser = _build_test_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["azdo", "stories", "query", "SRE", "--wiql", "SELECT 1"])


def test_remote_commands_show_helpful_message_when_no_remotes(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser(smith_config=SmithConfig(remotes={}, defaults={}))

    with pytest.raises(SystemExit):
        parser.parse_args(["gitlab-infra", "repos"])

    error_output = capsys.readouterr().err
    assert "No remotes configured" in error_output
    assert "smith config init" in error_output
