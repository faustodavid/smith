from __future__ import annotations

import json

import pytest

from smith.benchmark.runner import (
    BenchmarkEval,
    build_codex_tool_trace,
    execute_eval_run,
    render_tool_trace_markdown,
)


def test_build_codex_tool_trace_captures_arguments_and_result_preview():
    events = [
        {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "server": "github-benchmark",
                "tool": "search_code",
                "status": "completed",
                "arguments": {"query": 'org:openai "OPENAI_WEBHOOK_SECRET"'},
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": '{"total_count":5,"items":[{"path":"src/client.ts"}]}',
                        }
                    ]
                },
            },
        }
    ]

    trace = build_codex_tool_trace(events)

    assert trace == [
        {
            "arguments": {"query": 'org:openai "OPENAI_WEBHOOK_SECRET"'},
            "kind": "mcp_tool_call",
            "result_preview": '{"total_count":5,"items":[{"path":"src/client.ts"}]}',
            "server": "github-benchmark",
            "status": "completed",
            "step": 1,
            "tool": "search_code",
        }
    ]


def test_render_tool_trace_markdown_includes_auditable_arguments():
    tool_trace = [
        {
            "step": 1,
            "kind": "mcp_tool_call",
            "server": "smith-benchmark",
            "tool": "smith_cli",
            "status": "failed",
            "arguments": {"command": "smith github repos"},
            "result_preview": "github auth failed",
        }
    ]

    markdown = render_tool_trace_markdown(tool_trace)

    assert "`smith-benchmark:smith_cli` `failed`" in markdown
    assert '"command": "smith github repos"' in markdown
    assert "github auth failed" in markdown


@pytest.mark.asyncio
async def test_execute_eval_run_writes_tool_trace_artifacts(tmp_path, monkeypatch):
    eval_case = BenchmarkEval(
        id=1,
        name="test-eval",
        prompt="Find the config.",
        expected_output="answer",
        expectations=["includes source"],
    )

    async def fake_run_agent_once(*, config_name: str, model: str, prompt: str, executor: str):
        assert config_name == "smith_skill"
        assert model == "gpt-5.4"
        assert prompt == "Find the config."
        assert executor == "codex"
        return (
            "answer",
            "# Transcript\n",
            {
                "errors_encountered": 0,
                "files_created": ["final_answer.md", "transcript.md", "tool_trace.json", "tool_trace.md"],
                "tool_calls": {"smith-benchmark:smith_cli": 1},
                "total_steps": 1,
                "total_tool_calls": 1,
            },
            {"total_tokens": 42},
            [
                {
                    "step": 1,
                    "kind": "mcp_tool_call",
                    "server": "smith-benchmark",
                    "tool": "smith_cli",
                    "status": "completed",
                    "arguments": {"command": "smith github repos"},
                }
            ],
        )

    def fake_grade_run_directory(run_dir, expectations):
        assert expectations == ["includes source"]
        (run_dir / "grading.json").write_text(
            json.dumps(
                {
                    "summary": {"pass_rate": 1.0, "passed": 1, "failed": 0},
                    "expectations": [],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

    monkeypatch.setattr("smith.benchmark.runner.run_agent_once", fake_run_agent_once)
    monkeypatch.setattr("smith.benchmark.runner.grade_run_directory", fake_grade_run_directory)

    await execute_eval_run(
        workspace=tmp_path,
        eval_case=eval_case,
        config_name="smith_skill",
        run_number=1,
        model="gpt-5.4",
        executor="codex",
    )

    outputs_dir = tmp_path / "eval-1" / "smith_skill" / "run-1" / "outputs"
    tool_trace_json = json.loads((outputs_dir / "tool_trace.json").read_text())
    tool_trace_md = (outputs_dir / "tool_trace.md").read_text()
    metrics = json.loads((outputs_dir / "metrics.json").read_text())

    assert tool_trace_json[0]["arguments"] == {"command": "smith github repos"}
    assert "`smith-benchmark:smith_cli` `completed`" in tool_trace_md
    assert metrics["tool_trace_entries"] == 1
