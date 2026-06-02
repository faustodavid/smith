from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest
import requests
from tests.support import make_runtime_config

from smith.providers.azdo import AzdoProvider
from smith.providers.github import GitHubProvider
from smith.providers.gitlab import GitLabProvider


def _required_env(names: list[str], *, label: str) -> dict[str, str]:
    values = {name: os.getenv(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        pytest.skip(f"missing {label} integration env: {', '.join(missing)}")
    return values


@pytest.fixture
def github_smoke_env() -> dict[str, Any]:
    values = _required_env(
        [
            "GITHUB_ORG",
            "GITHUB_TOKEN",
            "SMITH_TEST_GITHUB_REPO",
            "SMITH_TEST_GITHUB_PR_ID",
            "SMITH_TEST_GITHUB_RUN_ID",
            "SMITH_TEST_GITHUB_ISSUE_ID",
        ],
        label="GitHub",
    )
    values["SMITH_TEST_GITHUB_SEARCH_QUERY"] = os.getenv("SMITH_TEST_GITHUB_SEARCH_QUERY", "").strip() or values["SMITH_TEST_GITHUB_REPO"]
    return values


@pytest.fixture
def github_provider(github_smoke_env: dict[str, Any]) -> Iterator[GitHubProvider]:
    session = requests.Session()
    try:
        yield GitHubProvider(
            config=make_runtime_config(github_org=github_smoke_env["GITHUB_ORG"]),
            session=session,
        )
    finally:
        session.close()


@pytest.fixture
def gitlab_smoke_env() -> dict[str, Any]:
    values = _required_env(
        [
            "GITLAB_TOKEN",
            "SMITH_TEST_GITLAB_REPO",
            "SMITH_TEST_GITLAB_MR_ID",
            "SMITH_TEST_GITLAB_PIPELINE_ID",
            "SMITH_TEST_GITLAB_ISSUE_ID",
        ],
        label="GitLab",
    )
    values["SMITH_TEST_GITLAB_SEARCH_QUERY"] = os.getenv("SMITH_TEST_GITLAB_SEARCH_QUERY", "").strip() or values["SMITH_TEST_GITLAB_REPO"]
    return values


@pytest.fixture
def gitlab_provider(gitlab_smoke_env: dict[str, Any]) -> Iterator[GitLabProvider]:
    gitlab_org = gitlab_smoke_env["SMITH_TEST_GITLAB_REPO"].rsplit("/", 1)[0]
    session = requests.Session()
    try:
        yield GitLabProvider(
            config=make_runtime_config(),
            session=session,
            gitlab_org=gitlab_org,
        )
    finally:
        session.close()


@pytest.fixture
def azdo_smoke_env() -> dict[str, Any]:
    values = _required_env(
        [
            "AZURE_DEVOPS_ORG",
            "AZURE_DEVOPS_PAT",
            "SMITH_TEST_AZDO_PROJECT",
            "SMITH_TEST_AZDO_REPO",
            "SMITH_TEST_AZDO_PR_ID",
            "SMITH_TEST_AZDO_BUILD_ID",
            "SMITH_TEST_AZDO_WORK_ITEM_ID",
        ],
        label="Azure DevOps",
    )
    values["SMITH_TEST_AZDO_SEARCH_QUERY"] = os.getenv("SMITH_TEST_AZDO_SEARCH_QUERY", "").strip() or values["SMITH_TEST_AZDO_REPO"]
    return values


@pytest.fixture
def azdo_provider(azdo_smoke_env: dict[str, Any]) -> Iterator[AzdoProvider]:
    session = requests.Session()
    try:
        yield AzdoProvider(
            config=make_runtime_config(azdo_org=azdo_smoke_env["AZURE_DEVOPS_ORG"]),
            session=session,
            token_env="AZURE_DEVOPS_PAT",
        )
    finally:
        session.close()
