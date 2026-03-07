from __future__ import annotations

from typing import Any

from smith.providers.github import GitHubProvider


def test_github_provider_smoke_repository_and_search(
    github_provider: GitHubProvider,
    github_smoke_env: dict[str, Any],
) -> None:
    projects = github_provider.list_projects()
    assert any(entry.get("name") == github_smoke_env["GITHUB_ORG"] for entry in projects)

    repos = github_provider.list_repositories()
    assert any(entry.get("name") == github_smoke_env["SMITH_TEST_GITHUB_REPO"] for entry in repos)

    search = github_provider.search_code(
        query=github_smoke_env["SMITH_TEST_GITHUB_SEARCH_QUERY"],
        project=github_smoke_env["SMITH_TEST_GITHUB_REPO"],
        repos=None,
        skip=0,
        take=5,
    )
    assert isinstance(search["results"], list)
    assert search["matchesCount"] >= len(search["results"])


def test_github_provider_smoke_pr_build_and_issue(
    github_provider: GitHubProvider,
    github_smoke_env: dict[str, Any],
) -> None:
    repo = github_smoke_env["SMITH_TEST_GITHUB_REPO"]
    pull_request_id = int(github_smoke_env["SMITH_TEST_GITHUB_PR_ID"])
    build_id = int(github_smoke_env["SMITH_TEST_GITHUB_RUN_ID"])
    issue_id = int(github_smoke_env["SMITH_TEST_GITHUB_ISSUE_ID"])

    pull_request = github_provider.get_pull_request(repo=repo, pull_request_id=pull_request_id)
    assert pull_request["pull_request"]["pullRequestId"] == pull_request_id
    assert isinstance(pull_request["threads"], list)

    build_log = github_provider.get_build_log(repo=repo, build_id=build_id)
    assert build_log["metadata"]["build_id"] == build_id
    assert isinstance(build_log["logs"], list)

    issue = github_provider.get_ticket_by_id(repo=repo, work_item_id=issue_id)
    assert int(issue["id"]) == issue_id
