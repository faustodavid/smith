from __future__ import annotations

from smith.benchmark.grading import BENCHMARK_EXPECTATIONS, build_grading_result

GOOD_ANSWER = "\n".join(
    [
        "# Findings",
        "",
        (
            "- openai/openai-python reads `OPENAI_WEBHOOK_SECRET` in `/src/openai/_client.py`. "
            "The webhook verifier lives in `/src/openai/resources/webhooks/webhooks.py`, where "
            "`unwrap` parses and verifies the payload and `verify_signature` performs "
            "signature-only verification."
        ),
        (
            "- openai/openai-node reads `OPENAI_WEBHOOK_SECRET` in `/src/client.ts`. "
            "The webhook verifier lives in `/src/resources/webhooks/webhooks.ts`, where "
            "`unwrap` parses and verifies the payload and `verifySignature` performs "
            "signature-only verification."
        ),
        (
            "- openai/openai-go reads `OPENAI_WEBHOOK_SECRET` in `/client.go`. "
            "The webhook verifier lives in `/webhooks/webhook.go`, where `Unwrap` parses "
            "and verifies the payload and `VerifySignature` performs signature-only "
            "verification."
        ),
        (
            "- openai/openai-ruby reads `OPENAI_WEBHOOK_SECRET` in `/lib/openai/client.rb`. "
            "The webhook verifier lives in `/lib/openai/resources/webhooks.rb`, where "
            "`unwrap` parses and verifies the payload and `verify_signature` performs "
            "signature-only verification."
        ),
        (
            "- openai/openai-java reads `OPENAI_WEBHOOK_SECRET` in "
            "`/openai-java-core/src/main/kotlin/com/openai/core/ClientOptions.kt`. "
            "The webhook verifier lives in "
            "`/openai-java-core/src/main/kotlin/com/openai/services/blocking/"
            "WebhookServiceImpl.kt`, where `unwrap` parses and verifies the payload "
            "and `verifySignature` performs signature-only verification."
        ),
        "",
        "## Sources",
        "- openai/openai-python:/src/openai/resources/webhooks/webhooks.py",
        "- openai/openai-node:/src/resources/webhooks/webhooks.ts",
        "- openai/openai-go:/webhooks/webhook.go",
        "- openai/openai-ruby:/lib/openai/resources/webhooks.rb",
        (
            "- openai/openai-java:/openai-java-core/src/main/kotlin/com/openai/services/"
            "blocking/WebhookServiceImpl.kt"
        ),
    ]
)

GOOD_ANSWER_WITH_BARE_REPO_SOURCES = "\n".join(
    [
        "# Findings",
        "",
        (
            "- openai/openai-python reads `OPENAI_WEBHOOK_SECRET` in `/src/openai/_client.py`. "
            "The helper file is `/src/openai/resources/webhooks/webhooks.py`, with `unwrap` "
            "and `verify_signature`."
        ),
        (
            "- openai/openai-node reads `OPENAI_WEBHOOK_SECRET` in `/src/client.ts`. "
            "The helper file is `/src/resources/webhooks/webhooks.ts`, with `unwrap` "
            "and `verifySignature`."
        ),
        (
            "- openai/openai-go reads `OPENAI_WEBHOOK_SECRET` in `/client.go`. "
            "The helper file is `/webhooks/webhook.go`, with `Unwrap` and `VerifySignature`."
        ),
        (
            "- openai/openai-ruby reads `OPENAI_WEBHOOK_SECRET` in `/lib/openai/client.rb`. "
            "The helper file is `/lib/openai/resources/webhooks.rb`, with `unwrap` "
            "and `verify_signature`."
        ),
        (
            "- openai/openai-java reads `OPENAI_WEBHOOK_SECRET` in "
            "`/openai-java-core/src/main/kotlin/com/openai/core/ClientOptions.kt`. "
            "The helper file is `/openai-java-core/src/main/kotlin/com/openai/services/"
            "blocking/WebhookServiceImpl.kt`, with `unwrap` and `verifySignature`."
        ),
        "",
        "**Sources**",
        "- openai-python:/src/openai/resources/webhooks/webhooks.py",
        "- openai-node:/src/resources/webhooks/webhooks.ts",
        "- openai-go:/webhooks/webhook.go",
        "- openai-ruby:/lib/openai/resources/webhooks.rb",
        (
            "- openai-java:/openai-java-core/src/main/kotlin/com/openai/services/blocking/"
            "WebhookServiceImpl.kt"
        ),
    ]
)


def test_full_answer_passes_all_expectations():
    grading = build_grading_result(answer_text=GOOD_ANSWER, expectations=BENCHMARK_EXPECTATIONS)

    assert grading["summary"]["passed"] == len(BENCHMARK_EXPECTATIONS)
    assert grading["summary"]["failed"] == 0


def test_missing_repo_declaration_fails():
    answer = GOOD_ANSWER.replace("openai/openai-go", "openai/openai-dotnet")

    grading = build_grading_result(answer_text=answer, expectations=BENCHMARK_EXPECTATIONS)

    failing = {item["text"] for item in grading["expectations"] if not item["passed"]}
    assert "includes openai/openai-go" in failing
    assert "Sources include only qualifying repos" in failing


def test_missing_helper_path_fails():
    answer = GOOD_ANSWER.replace("/src/resources/webhooks/webhooks.ts", "/src/resources/webhooks/unknown.ts")

    grading = build_grading_result(answer_text=answer, expectations=BENCHMARK_EXPECTATIONS)

    failing = {item["text"] for item in grading["expectations"] if not item["passed"]}
    assert "includes openai/openai-node helper path /src/resources/webhooks/webhooks.ts" in failing


def test_wrong_helper_name_fails():
    answer = GOOD_ANSWER.replace("verifySignature", "verifyWebhook")
    answer = answer.replace("VerifySignature", "verifyWebhook")

    grading = build_grading_result(answer_text=answer, expectations=BENCHMARK_EXPECTATIONS)

    failing = {item["text"] for item in grading["expectations"] if not item["passed"]}
    assert "openai/openai-node helper names unwrap + verifySignature" in failing


def test_missing_sources_section_fails():
    answer = GOOD_ANSWER.split("## Sources", 1)[0].strip()

    grading = build_grading_result(answer_text=answer, expectations=BENCHMARK_EXPECTATIONS)

    failing = {item["text"] for item in grading["expectations"] if not item["passed"]}
    assert "includes a Sources section with repo:path entries" in failing


def test_bare_repo_names_in_sources_are_accepted():
    grading = build_grading_result(
        answer_text=GOOD_ANSWER_WITH_BARE_REPO_SOURCES,
        expectations=BENCHMARK_EXPECTATIONS,
    )

    assert grading["summary"]["passed"] == len(BENCHMARK_EXPECTATIONS)
    assert grading["summary"]["failed"] == 0
