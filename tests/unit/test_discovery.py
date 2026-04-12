from __future__ import annotations

import pytest

from smith.discovery import (
    DEFAULT_DISCOVERY_TAKE,
    MAX_DISCOVERY_TAKE,
    DiscoveryQuery,
    build_discovery_payload,
)


def test_discovery_query_defaults_and_caps_take() -> None:
    default_query = DiscoveryQuery.create()
    capped_query = DiscoveryQuery.create(skip=7, take=999)

    assert default_query.skip == 0
    assert default_query.take == DEFAULT_DISCOVERY_TAKE
    assert default_query.warnings == ()

    assert capped_query.skip == 7
    assert capped_query.take == MAX_DISCOVERY_TAKE
    assert capped_query.warnings == (f"`--take` capped at {MAX_DISCOVERY_TAKE}.",)


def test_discovery_query_compile_grep_rejects_invalid_regex() -> None:
    with pytest.raises(ValueError, match="Invalid regex pattern"):
        DiscoveryQuery.create(grep="(").compile_grep()


def test_discovery_query_caches_compiled_grep_and_derives_server_search_term() -> None:
    query = DiscoveryQuery.create(grep="platform/api")

    assert query.compile_grep() is query.compile_grep()
    assert query.server_search_term() == "platform/api"
    assert DiscoveryQuery.create(grep="^platform").server_search_term() is None


def test_build_discovery_payload_includes_truncation_warning() -> None:
    payload = build_discovery_payload(
        rows=[{"name": "platform/api"}],
        query=DiscoveryQuery.create(skip=0, take=1),
        has_more=True,
        subject="groups",
    )

    assert payload == {
        "results": [{"name": "platform/api"}],
        "returned_count": 1,
        "has_more": True,
        "warnings": ["showing 1 matching groups; use --skip/--take to see more."],
        "partial": True,
    }
