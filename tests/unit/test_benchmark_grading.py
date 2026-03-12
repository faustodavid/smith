from __future__ import annotations

from smith.benchmark.grading import build_grading_result

EXPECTATIONS = [
    "includes grafana/alloy",
    "includes grafana/agent",
    "includes internal/component/otelcol/exporter/loki/loki.go",
    'maps grafana/alloy to alloy:"forward_to,attr"',
    'maps grafana/agent to river:"forward_to,attr"',
    "does not present docs-only hits as implementation repos",
    "includes a Sources section with repo:path",
]

RETENTION_EXPECTATIONS = [
    "includes grafana/loki",
    "includes loki.limits_config.retention_period",
    "maps retention_period to 1 year",
    "includes loki.compactor.retention_enabled",
    "includes loki.compactor.delete_request_store",
    "states delete_request_store is required when retention is enabled",
    "does not recommend deprecated table_manager retention",
    "includes sources for values.yaml and retention.md",
    "includes a Sources section with repo:path",
]


GOOD_ANSWER = """
# Findings

- grafana/alloy uses `/internal/component/otelcol/exporter/loki/loki.go` and `Arguments.ForwardTo` is tagged `alloy:"forward_to,attr"`.
- grafana/agent uses `/internal/component/otelcol/exporter/loki/loki.go` and `Arguments.ForwardTo` is tagged `river:"forward_to,attr"`.

## Sources
- grafana/alloy:/internal/component/otelcol/exporter/loki/loki.go
- grafana/agent:/internal/component/otelcol/exporter/loki/loki.go
""".strip()

GOOD_RETENTION_ANSWER = """
# Findings

Use the `grafana/loki` Helm chart values, not the deprecated table_manager path.

```yaml
loki:
  limits_config:
    retention_period: 8760h
  compactor:
    retention_enabled: true
    delete_request_store: s3
```

`delete_request_store` must be set when retention is enabled.

## Sources
- grafana/loki:/production/helm/loki/values.yaml
- grafana/loki:/docs/sources/operations/storage/retention.md
""".strip()

GOOD_RETENTION_ANSWER_WITH_FLEXIBLE_SOURCES = """
Set these Helm values on the official Loki chart:

```yaml
loki:
  limits_config:
    retention_period: 8760h
  compactor:
    retention_enabled: true
    delete_request_store: s3
```

`8760h` is the exact 1-year retention value to use.
`delete_request_store` must be set when retention is enabled.
Do not use the deprecated `table_manager` retention path.

**Sources**
- loki:/docs/sources/operations/storage/retention.md
- loki:production/helm/loki/values.yaml
""".strip()

GOOD_RETENTION_ANSWER_WITH_REVERSED_REQUIREMENT = """
For the official `grafana/loki` Helm chart, set these Helm values under `loki`:

```yaml
loki:
  limits_config:
    retention_period: 8760h
  compactor:
    retention_enabled: true
    delete_request_store: s3
```

The compactor setting the Loki docs explicitly say is required when retention is enabled is:

- `loki.compactor.delete_request_store`

Do not use the deprecated `table_manager` retention path.

**Sources**
- grafana/loki:production/helm/loki/values.yaml
- grafana/loki:docs/sources/operations/storage/retention.md
""".strip()


def test_good_answer_passes_all_expectations():
    grading = build_grading_result(answer_text=GOOD_ANSWER, expectations=EXPECTATIONS)

    assert grading["summary"]["passed"] == len(EXPECTATIONS)
    assert grading["summary"]["failed"] == 0


def test_missing_agent_repo_fails():
    answer = GOOD_ANSWER.replace("grafana/agent", "grafana/tempo")

    grading = build_grading_result(answer_text=answer, expectations=EXPECTATIONS)

    failing = {item["text"] for item in grading["expectations"] if not item["passed"]}
    assert "includes grafana/agent" in failing


def test_swapped_struct_tags_fail():
    answer = GOOD_ANSWER.replace('alloy:"forward_to,attr"', 'river:"forward_to,attr"', 1)

    grading = build_grading_result(answer_text=answer, expectations=EXPECTATIONS)

    failing = {item["text"] for item in grading["expectations"] if not item["passed"]}
    assert 'maps grafana/alloy to alloy:"forward_to,attr"' in failing


def test_missing_sources_section_fails():
    answer = GOOD_ANSWER.split("## Sources", 1)[0].strip()

    grading = build_grading_result(answer_text=answer, expectations=EXPECTATIONS)

    failing = {item["text"] for item in grading["expectations"] if not item["passed"]}
    assert "includes a Sources section with repo:path" in failing


def test_docs_only_repo_in_sources_fails():
    answer = GOOD_ANSWER + "\n- grafana/loki:/docs/sources/reference/components/otelcol/otelcol.exporter.loki.md\n"

    grading = build_grading_result(answer_text=answer, expectations=EXPECTATIONS)

    failing = {item["text"] for item in grading["expectations"] if not item["passed"]}
    assert "does not present docs-only hits as implementation repos" in failing


def test_good_retention_answer_passes_all_expectations():
    grading = build_grading_result(answer_text=GOOD_RETENTION_ANSWER, expectations=RETENTION_EXPECTATIONS)

    assert grading["summary"]["passed"] == len(RETENTION_EXPECTATIONS)
    assert grading["summary"]["failed"] == 0


def test_retention_answer_requires_one_year_duration():
    answer = GOOD_RETENTION_ANSWER.replace("8760h", "744h", 1)

    grading = build_grading_result(answer_text=answer, expectations=RETENTION_EXPECTATIONS)

    failing = {item["text"] for item in grading["expectations"] if not item["passed"]}
    assert "maps retention_period to 1 year" in failing


def test_retention_answer_requires_delete_request_store_requirement_statement():
    answer = GOOD_RETENTION_ANSWER.replace(
        "`delete_request_store` must be set when retention is enabled.",
        "`delete_request_store` configures the backend.",
        1,
    )

    grading = build_grading_result(answer_text=answer, expectations=RETENTION_EXPECTATIONS)

    failing = {item["text"] for item in grading["expectations"] if not item["passed"]}
    assert "states delete_request_store is required when retention is enabled" in failing


def test_retention_answer_rejects_table_manager_recommendation():
    answer = GOOD_RETENTION_ANSWER.replace(
        "Use the `grafana/loki` Helm chart values, not the deprecated table_manager path.",
        "Use the deprecated table_manager retention path in the Helm chart.",
        1,
    )

    grading = build_grading_result(answer_text=answer, expectations=RETENTION_EXPECTATIONS)

    failing = {item["text"] for item in grading["expectations"] if not item["passed"]}
    assert "does not recommend deprecated table_manager retention" in failing


def test_retention_answer_requires_values_and_retention_sources():
    answer = GOOD_RETENTION_ANSWER.replace(
        "- grafana/loki:/docs/sources/operations/storage/retention.md",
        "- grafana/loki:/docs/sources/setup/install/helm/install-scalable.md",
        1,
    )

    grading = build_grading_result(answer_text=answer, expectations=RETENTION_EXPECTATIONS)

    failing = {item["text"] for item in grading["expectations"] if not item["passed"]}
    assert "includes sources for values.yaml and retention.md" in failing


def test_retention_answer_accepts_bold_sources_header_and_bare_repo_names():
    grading = build_grading_result(
        answer_text=GOOD_RETENTION_ANSWER_WITH_FLEXIBLE_SOURCES,
        expectations=RETENTION_EXPECTATIONS,
    )

    assert grading["summary"]["passed"] == len(RETENTION_EXPECTATIONS)
    assert grading["summary"]["failed"] == 0


def test_retention_answer_accepts_reverse_requirement_order():
    grading = build_grading_result(
        answer_text=GOOD_RETENTION_ANSWER_WITH_REVERSED_REQUIREMENT,
        expectations=RETENTION_EXPECTATIONS,
    )

    assert grading["summary"]["passed"] == len(RETENTION_EXPECTATIONS)
    assert grading["summary"]["failed"] == 0
