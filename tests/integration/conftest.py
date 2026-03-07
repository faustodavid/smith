from __future__ import annotations

import os
from typing import Any

import pytest
import requests
from tests.support import make_runtime_config

from smith.providers.azdo import AzdoProvider
from smith.providers.github import GitHubProvider


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
    values["SMITH_TEST_GITHUB_SEARCH_QUERY"] = (
        os.getenv("SMITH_TEST_GITHUB_SEARCH_QUERY", "").strip()
        or values["SMITH_TEST_GITHUB_REPO"]
    )
    return values


@pytest.fixture
def github_provider(github_smoke_env: dict[str, Any]) -> GitHubProvider:
    return GitHubProvider(
        config=make_runtime_config(github_org=github_smoke_env["GITHUB_ORG"]),
        session=requests.Session(),
    )


@pytest.fixture
def azdo_smoke_env() -> dict[str, Any]:
    values = _required_env(
        [
            "AZURE_DEVOPS_ORG",
            "SMITH_TEST_AZDO_PROJECT",
            "SMITH_TEST_AZDO_REPO",
            "SMITH_TEST_AZDO_PR_ID",
            "SMITH_TEST_AZDO_BUILD_ID",
            "SMITH_TEST_AZDO_WORK_ITEM_ID",
        ],
        label="Azure DevOps",
    )
    values["SMITH_TEST_AZDO_SEARCH_QUERY"] = (
        os.getenv("SMITH_TEST_AZDO_SEARCH_QUERY", "").strip()
        or values["SMITH_TEST_AZDO_REPO"]
    )
    return values


@pytest.fixture
def azdo_provider(azdo_smoke_env: dict[str, Any]) -> AzdoProvider:
    return AzdoProvider(
        config=make_runtime_config(azdo_org=azdo_smoke_env["AZURE_DEVOPS_ORG"]),
        session=requests.Session(),
    )
