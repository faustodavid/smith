from __future__ import annotations

import pytest

from smith.pipeline_listing import (
    DEFAULT_PIPELINE_LIST_TAKE,
    MAX_PIPELINE_LIST_TAKE,
    SHORT_STATUS,
    VALID_PIPELINE_STATUSES,
    DownstreamRef,
    JobRow,
    PipelineListQuery,
    build_job_dict,
    build_job_row,
    build_pipeline_list_payload,
    build_pipeline_row,
    parse_matrix_from_name,
    short_status,
)


def test_pipeline_list_query_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="status must be one of"):
        PipelineListQuery.create(statuses=["bogus"])


def test_pipeline_list_query_normalizes_statuses_and_dedupes() -> None:
    query = PipelineListQuery.create(statuses=["Running", "failed", "running", "  "])

    assert query.statuses == ("running", "failed")


def test_pipeline_list_query_rejects_invalid_regex() -> None:
    with pytest.raises(ValueError):
        PipelineListQuery.create(grep="(")


def test_pipeline_list_query_caps_take_and_warns() -> None:
    query = PipelineListQuery.create(take=MAX_PIPELINE_LIST_TAKE + 10)

    assert query.take == MAX_PIPELINE_LIST_TAKE
    assert query.requested_take == MAX_PIPELINE_LIST_TAKE + 10
    assert any("capped" in warning for warning in query.warnings)


def test_pipeline_list_query_defaults_are_sane() -> None:
    query = PipelineListQuery.create()

    assert query.take == DEFAULT_PIPELINE_LIST_TAKE
    assert query.skip == 0
    assert query.max_depth == 0
    assert query.statuses == ()
    assert query.warnings == ()


def test_valid_pipeline_statuses_contains_canonical_set() -> None:
    assert {"running", "failed", "success", "canceled"} <= VALID_PIPELINE_STATUSES


def test_build_pipeline_list_payload_flags_partial_on_truncation() -> None:
    rows = [
        build_pipeline_row(pipeline_id=i, project="p", status="running")
        for i in range(5)
    ]
    query = PipelineListQuery.create(take=2)

    payload = build_pipeline_list_payload(rows=rows, query=query)

    assert payload["returned_count"] == 2
    assert payload["total_count"] == 5
    assert payload["partial"] is True
    assert any("showing 2 of 5" in warning for warning in payload["warnings"])


def test_build_pipeline_list_payload_not_partial_when_full_window() -> None:
    rows = [
        build_pipeline_row(pipeline_id=i, project="p", status="success")
        for i in range(3)
    ]
    query = PipelineListQuery.create(take=5)

    payload = build_pipeline_list_payload(rows=rows, query=query)

    assert payload["returned_count"] == 3
    assert payload["total_count"] == 3
    assert payload["partial"] is False
    assert payload["warnings"] == []


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("success", "ok"),
        ("failed", "err"),
        ("running", "run"),
        ("skipped", "skip"),
        ("manual", "man"),
        ("canceled", "can"),
        ("pending", "pen"),
        ("created", "cre"),
        ("scheduled", "sch"),
        ("waiting_for_resource", "wait"),
        ("preparing", "prep"),
        ("Success", "ok"),
        (None, "-"),
        ("", "-"),
    ],
)
def test_short_status_maps_canonical_values(status: str | None, expected: str) -> None:
    assert short_status(status) == expected


def test_short_status_falls_back_to_prefix_for_unknown_values() -> None:
    assert short_status("exotic_state") == "exot"
    assert short_status("ok") == "ok"


def test_short_status_map_is_consistent_with_helper() -> None:
    for canonical, short in SHORT_STATUS.items():
        assert short_status(canonical) == short


@pytest.mark.parametrize(
    ("raw", "expected_base", "expected_matrix"),
    [
        ("unit [1/3]", "unit", (1, 3)),
        ("unit 1/3", "unit", (1, 3)),
        ("unit (2/4)", "unit", (2, 4)),
        ("unit[3/3]", "unit", (3, 3)),
        ("unit", "unit", None),
        ("build (ubuntu-latest, 3.11)", "build (ubuntu-latest, 3.11)", None),
        ("  unit  [1/2]  ", "unit", (1, 2)),
        ("", "", None),
        ("test 0/3", "test 0/3", None),  # index must be >= 1
        ("test 4/3", "test 4/3", None),  # index must be <= total
        ("[1/2]", "[1/2]", None),        # empty base -> no match
    ],
)
def test_parse_matrix_from_name_extracts_or_preserves_name(
    raw: str, expected_base: str, expected_matrix: tuple[int, int] | None
) -> None:
    base, matrix = parse_matrix_from_name(raw)
    assert base == expected_base
    assert matrix == expected_matrix


def test_build_job_row_parses_matrix_and_normalizes_fields() -> None:
    row = build_job_row(
        job_id="42",
        name="rspec 1/3",
        status="FAILED",
        stage=" test ",
        duration_s="65.5",
        allow_failure=1,
        manual=0,
        environment=" prod ",
        needs=[" compile ", ""],
    )

    assert row == JobRow(
        id=42,
        name="rspec",
        status="failed",
        stage="test",
        duration_s=65,
        matrix=(1, 3),
        allow_failure=True,
        manual=False,
        environment="prod",
        needs=("compile",),
        downstream=None,
    )


def test_build_job_row_preserves_explicit_matrix_over_parsed_one() -> None:
    row = build_job_row(
        job_id=7,
        name="test [1/3]",
        status="success",
        matrix=(2, 5),
    )

    assert row.matrix == (2, 5)
    assert row.name == "test"


def test_build_job_dict_matches_job_row_serialization() -> None:
    downstream = DownstreamRef(project="ops/infra", pipeline_id=1122, status="Created")
    kwargs = {
        "job_id": "42",
        "name": "rspec 1/3",
        "status": "FAILED",
        "stage": " test ",
        "duration_s": "65.5",
        "allow_failure": 1,
        "manual": 0,
        "environment": " prod ",
        "needs": [" compile ", ""],
        "downstream": downstream,
    }

    assert build_job_dict(**kwargs) == build_job_row(**kwargs).to_dict()


def test_job_row_to_dict_serialises_downstream_and_needs() -> None:
    downstream = DownstreamRef(project="ops/infra", pipeline_id=1122, status="Created")
    row = JobRow(
        id=31,
        name="prod_up",
        status="manual",
        manual=True,
        environment="prod",
        needs=("staging_up",),
        downstream=downstream,
    )

    serialised = row.to_dict()

    assert serialised == {
        "id": 31,
        "name": "prod_up",
        "status": "manual",
        "stage": None,
        "duration_s": None,
        "matrix": None,
        "allow_failure": False,
        "manual": True,
        "environment": "prod",
        "needs": ["staging_up"],
        "downstream": {
            "project": "ops/infra",
            "pipeline_id": 1122,
            "status": "created",
        },
    }


def test_build_pipeline_row_omits_jobs_key_when_not_provided() -> None:
    row = build_pipeline_row(pipeline_id=1, project="p", status="running")
    assert "jobs" not in row


def test_build_pipeline_row_serialises_job_rows_when_provided() -> None:
    job = build_job_row(job_id=10, name="compile", status="success", stage="build", duration_s=30)
    row = build_pipeline_row(pipeline_id=1, project="p", status="running", jobs=[job])

    assert row["jobs"] == [job.to_dict()]


def test_build_pipeline_list_payload_status_filter_keeps_failed_jobs_and_ancestors() -> None:
    row = build_pipeline_row(
        pipeline_id=77,
        project="repo-a",
        status="failed",
        jobs=[
            build_job_row(job_id=10, name="compile", status="success", stage="build"),
            build_job_row(job_id=11, name="docs", status="success", stage="build"),
            build_job_row(job_id=20, name="unit", status="success", stage="test", needs=["compile"]),
            build_job_row(job_id=21, name="lint", status="failed", stage="test", needs=["compile"]),
            build_job_row(job_id=30, name="release", status="failed", stage="deploy", needs=["unit"]),
            build_job_row(job_id=40, name="notify", status="success", stage="post", needs=["release"]),
        ],
    )

    payload = build_pipeline_list_payload(
        rows=[row],
        query=PipelineListQuery.create(statuses=["failed"]),
    )

    jobs = payload["pipelines"][0]["jobs"]
    assert [job["id"] for job in jobs] == [10, 20, 21, 30]
    assert [job["name"] for job in jobs] == ["compile", "unit", "lint", "release"]
    assert payload["returned_count"] == 1
    assert payload["total_count"] == 1


def test_build_pipeline_list_payload_status_filter_includes_all_ambiguous_parent_jobs() -> None:
    row = build_pipeline_row(
        pipeline_id=88,
        project="repo-a",
        status="failed",
        jobs=[
            build_job_row(job_id=10, name="compile", status="success", stage="build"),
            build_job_row(job_id=21, name="unit 1/2", status="success", stage="test", needs=["compile"]),
            build_job_row(job_id=22, name="unit 2/2", status="success", stage="test", needs=["compile"]),
            build_job_row(job_id=30, name="deploy", status="failed", stage="deploy", needs=["unit"]),
        ],
    )

    payload = build_pipeline_list_payload(
        rows=[row],
        query=PipelineListQuery.create(statuses=["failed"]),
    )

    jobs = payload["pipelines"][0]["jobs"]
    assert [job["id"] for job in jobs] == [10, 21, 22, 30]
    assert [job["matrix"] for job in jobs] == [None, [1, 2], [2, 2], None]


def test_build_pipeline_list_payload_status_filter_includes_trigger_jobs_for_failed_children() -> None:
    root = build_pipeline_row(
        pipeline_id=1,
        project="repo-a",
        status="failed",
        jobs=[
            build_job_row(job_id=10, name="prepare", status="success", stage="build"),
            build_job_row(
                job_id=20,
                name="trigger-release",
                status="success",
                stage="deploy",
                needs=["prepare"],
                downstream=DownstreamRef(project="repo-a", pipeline_id=2, status="failed"),
            ),
        ],
    )
    child = build_pipeline_row(
        pipeline_id=2,
        project="repo-a",
        status="failed",
        jobs=[
            build_job_row(job_id=30, name="deploy", status="failed", stage="deploy"),
        ],
    )

    payload = build_pipeline_list_payload(
        rows=[root, child],
        query=PipelineListQuery.create(statuses=["failed"]),
    )

    root_jobs = payload["pipelines"][0]["jobs"]
    child_jobs = payload["pipelines"][1]["jobs"]
    assert [job["id"] for job in root_jobs] == [10, 20]
    assert [job["id"] for job in child_jobs] == [30]
