from __future__ import annotations

from typing import Any

from smith.providers.gitlab import GitLabProvider


def test_gitlab_provider_smoke_repository_and_search(
    gitlab_provider: GitLabProvider,
    gitlab_smoke_env: dict[str, Any],
) -> None:
    projects = gitlab_provider.list_projects()
    assert any(entry.get("name") == gitlab_smoke_env["GITLAB_GROUP"] for entry in projects)

    repos = gitlab_provider.list_repositories()
    assert any(entry.get("name") == gitlab_smoke_env["SMITH_TEST_GITLAB_REPO"] for entry in repos)

    search = gitlab_provider.search_code(
        query=gitlab_smoke_env["SMITH_TEST_GITLAB_SEARCH_QUERY"],
        project=gitlab_smoke_env["SMITH_TEST_GITLAB_REPO"],
        repos=None,
        skip=0,
        take=5,
    )
    assert isinstance(search["results"], list)
    assert search["matchesCount"] >= len(search["results"])


def test_gitlab_provider_smoke_pr_build_and_issue(
    gitlab_provider: GitLabProvider,
    gitlab_smoke_env: dict[str, Any],
) -> None:
    repo = gitlab_smoke_env["SMITH_TEST_GITLAB_REPO"]
    pull_request_id = int(gitlab_smoke_env["SMITH_TEST_GITLAB_MR_ID"])
    build_id = int(gitlab_smoke_env["SMITH_TEST_GITLAB_PIPELINE_ID"])
    issue_id = int(gitlab_smoke_env["SMITH_TEST_GITLAB_ISSUE_ID"])

    pull_request = gitlab_provider.get_pull_request(repo=repo, pull_request_id=pull_request_id)
    assert int(pull_request["pull_request"]["pullRequestId"]) == pull_request_id
    assert isinstance(pull_request["threads"], list)

    build_log = gitlab_provider.get_build_log(repo=repo, build_id=build_id)
    assert int(build_log["metadata"]["build_id"]) == build_id
    assert isinstance(build_log["logs"], list)

    issue = gitlab_provider.get_ticket_by_id(repo=repo, work_item_id=issue_id)
    assert int(issue["id"]) == issue_id
