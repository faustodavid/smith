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


GOOD_ANSWER = """
# Findings

- grafana/alloy uses `/internal/component/otelcol/exporter/loki/loki.go` and `Arguments.ForwardTo` is tagged `alloy:"forward_to,attr"`.
- grafana/agent uses `/internal/component/otelcol/exporter/loki/loki.go` and `Arguments.ForwardTo` is tagged `river:"forward_to,attr"`.

## Sources
- grafana/alloy:/internal/component/otelcol/exporter/loki/loki.go
- grafana/agent:/internal/component/otelcol/exporter/loki/loki.go
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
