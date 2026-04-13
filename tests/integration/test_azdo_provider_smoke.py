from __future__ import annotations

from typing import Any

from smith.providers.azdo import AzdoProvider


def test_azdo_provider_smoke_repository_and_search(
    azdo_provider: AzdoProvider,
    azdo_smoke_env: dict[str, Any],
) -> None:
    projects = azdo_provider.list_projects()
    assert any(entry.get("name") == azdo_smoke_env["SMITH_TEST_AZDO_PROJECT"] for entry in projects)

    repos = azdo_provider.list_repositories(azdo_smoke_env["SMITH_TEST_AZDO_PROJECT"])
    assert any(entry.get("name") == azdo_smoke_env["SMITH_TEST_AZDO_REPO"] for entry in repos)

    search = azdo_provider.search_code(
        query=azdo_smoke_env["SMITH_TEST_AZDO_SEARCH_QUERY"],
        project=azdo_smoke_env["SMITH_TEST_AZDO_PROJECT"],
        repos=[azdo_smoke_env["SMITH_TEST_AZDO_REPO"]],
        skip=0,
        take=5,
    )
    assert isinstance(search["results"], list)
    assert search["matchesCount"] >= len(search["results"])


def test_azdo_provider_smoke_pr_build_and_work_item(
    azdo_provider: AzdoProvider,
    azdo_smoke_env: dict[str, Any],
) -> None:
    project = azdo_smoke_env["SMITH_TEST_AZDO_PROJECT"]
    repo = azdo_smoke_env["SMITH_TEST_AZDO_REPO"]
    pull_request_id = int(azdo_smoke_env["SMITH_TEST_AZDO_PR_ID"])
    build_id = int(azdo_smoke_env["SMITH_TEST_AZDO_BUILD_ID"])
    work_item_id = int(azdo_smoke_env["SMITH_TEST_AZDO_WORK_ITEM_ID"])

    pull_request = azdo_provider.get_pull_request(
        project=project,
        repo=repo,
        pull_request_id=pull_request_id,
    )
    assert pull_request["pull_request"]["pullRequestId"] == pull_request_id
    assert isinstance(pull_request["threads"], list)

    build_log = azdo_provider.get_build_log(project=project, build_id=build_id)
    assert build_log["metadata"]["build_id"] == build_id
    assert isinstance(build_log["logs"], list)

    work_item = azdo_provider.get_ticket_by_id(project=project, work_item_id=work_item_id)
    assert int(work_item["id"]) == work_item_id
