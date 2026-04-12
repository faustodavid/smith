from __future__ import annotations

import json
from pathlib import Path

import pytest

from smith.benchmark.constants import BENCHMARK_GITHUB_ORG
from smith.benchmark.runner import BenchmarkEval, aggregate_workspace
from smith.benchmark.smith_cli import InProcessSmithCliRunner, build_smith_cli_subprocess, validate_smith_cli_command
from smith.config import RemoteConfig, SmithConfig, load_config, save_config


def _write_benchmark_run(
    tmp_path,
    *,
    config_name: str,
    pass_rate: float,
    time_seconds: float,
    tokens: int,
) -> None:
    run_dir = tmp_path / "eval-1" / config_name / "run-1"
    outputs_dir = run_dir / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "grading.json").write_text(
        json.dumps(
            {
                "summary": {
                    "pass_rate": pass_rate,
                    "passed": 1 if pass_rate else 0,
                    "failed": 0 if pass_rate else 1,
                    "total": 1,
                },
                "expectations": [],
                "user_notes_summary": {"uncertainties": []},
            }
        )
    )
    (run_dir / "timing.json").write_text(json.dumps({"total_duration_seconds": time_seconds, "total_tokens": tokens}))
    (outputs_dir / "metrics.json").write_text(json.dumps({"total_tool_calls": 1, "errors_encountered": 0}))


def test_validate_smith_cli_command_accepts_global_code_search() -> None:
    tokens = validate_smith_cli_command('code search "otelcol.exporter.loki" --take 5')

    assert tokens == ["code", "search", "otelcol.exporter.loki", "--take", "5"]


def test_validate_smith_cli_command_rejects_non_github_grep():
    with pytest.raises(ValueError):
        validate_smith_cli_command("azdo code grep SRE repo pattern")


def test_validate_smith_cli_command_allows_prefixed_smith_global_flags():
    tokens = validate_smith_cli_command('smith --verbose --format json code search "otelcol.exporter.loki" --take 5')

    assert tokens == ["code", "search", "otelcol.exporter.loki", "--take", "5"]


def test_validate_smith_cli_command_accepts_configured_github_remote_search(tmp_path: Path) -> None:
    config_path = tmp_path / "smith.yaml"
    save_config(
        SmithConfig(
            remotes={
                "github-public": RemoteConfig(
                    name="github-public",
                    provider="github",
                    org=BENCHMARK_GITHUB_ORG,
                    host="github.com",
                    token_env="GITHUB_TOKEN",
                    enabled=True,
                    api_url="https://api.github.com",
                )
            },
            defaults={},
        ),
        config_path=config_path,
    )

    tokens = validate_smith_cli_command(
        'github-public code search "otelcol.exporter.loki" --take 5',
        env={"SMITH_CONFIG": str(config_path)},
    )

    assert tokens == ["github-public", "code", "search", "otelcol.exporter.loki", "--take", "5"]


def test_build_smith_cli_subprocess_injects_benchmark_config_and_local_src_path():
    argv, env = build_smith_cli_subprocess("github repos")

    assert argv[:3] == [argv[0], "-m", "smith.cli.main"]
    assert argv[3:] == ["github", "repos"]
    config_text = Path(env["SMITH_CONFIG"]).read_text()
    assert BENCHMARK_GITHUB_ORG in config_text
    assert "src" in env["PYTHONPATH"]


def test_build_smith_cli_subprocess_preserves_explicit_smith_config(tmp_path: Path) -> None:
    config_path = tmp_path / "smith.yaml"
    save_config(
        SmithConfig(
            remotes={
                "github-public": RemoteConfig(
                    name="github-public",
                    provider="github",
                    org=BENCHMARK_GITHUB_ORG,
                    host="github.com",
                    token_env="GITHUB_TOKEN",
                    enabled=True,
                    api_url="https://api.github.com",
                )
            },
            defaults={},
        ),
        config_path=config_path,
    )

    argv, env = build_smith_cli_subprocess(
        "github-public repos",
        env={"SMITH_CONFIG": str(config_path)},
    )

    assert argv[3:] == ["github-public", "repos"]
    assert env["SMITH_CONFIG"] == str(config_path)


def test_build_smith_cli_subprocess_preserves_explicit_benchmark_github_api_url() -> None:
    api_url = "http://127.0.0.1:4010/api/github"

    argv, env = build_smith_cli_subprocess(
        "github repos",
        env={"GITHUB_API_URL": api_url},
    )

    loaded_config = load_config(config_path=Path(env["SMITH_CONFIG"]))

    assert argv[3:] == ["github", "repos"]
    assert loaded_config.remotes["github"].api_url == api_url


def test_in_process_runner_client_cache_keys_on_api_url_and_smith_config(monkeypatch: pytest.MonkeyPatch) -> None:
    created_clients: list[object] = []

    def _fake_client() -> object:
        client = object()
        created_clients.append(client)
        return client

    monkeypatch.setattr("smith.benchmark.smith_cli.SmithClient", _fake_client)
    runner = InProcessSmithCliRunner()

    first = runner._get_or_create_client(
        {"GITHUB_API_URL": "http://127.0.0.1:4010/api/github", "SMITH_CONFIG": "/tmp/config-a.yaml"}
    )
    second = runner._get_or_create_client(
        {"GITHUB_API_URL": "http://127.0.0.1:4010/api/github/", "SMITH_CONFIG": "/tmp/config-a.yaml"}
    )
    third = runner._get_or_create_client(
        {"GITHUB_API_URL": "http://127.0.0.1:4020/api/github", "SMITH_CONFIG": "/tmp/config-a.yaml"}
    )
    fourth = runner._get_or_create_client(
        {"GITHUB_API_URL": "http://127.0.0.1:4020/api/github", "SMITH_CONFIG": "/tmp/config-b.yaml"}
    )

    assert first is second
    assert third is not second
    assert fourth is not third
    assert len(created_clients) == 3


def test_aggregate_workspace_skips_missing_configs_for_single_config_run(tmp_path):
    _write_benchmark_run(
        tmp_path,
        config_name="smith_skill",
        pass_rate=1.0,
        time_seconds=12.3,
        tokens=456,
    )

    benchmark, markdown = aggregate_workspace(
        workspace=tmp_path,
        evals_run=[BenchmarkEval(id=1, name="eval", prompt="prompt", expected_output="", expectations=[])],
        model="gpt-5",
        executor="openai",
        selected_configs=["smith_skill"],
    )

    assert benchmark["metadata"]["configurations_run"] == ["smith_skill"]
    assert "smith_skill" in benchmark["run_summary"]
    assert "github_mcp" not in benchmark["run_summary"]
    assert "delta" not in benchmark["run_summary"]
    assert "| smith_skill | 1.00 | 12.30 | 456 |" in markdown
    assert "| github_mcp |" not in markdown
    assert "Only `smith_skill` runs are included in this workspace." in markdown


def test_aggregate_workspace_keeps_delta_when_both_configs_are_present(tmp_path):
    _write_benchmark_run(
        tmp_path,
        config_name="smith_skill",
        pass_rate=1.0,
        time_seconds=12.3,
        tokens=456,
    )
    _write_benchmark_run(
        tmp_path,
        config_name="github_mcp",
        pass_rate=0.5,
        time_seconds=8.0,
        tokens=400,
    )

    benchmark, markdown = aggregate_workspace(
        workspace=tmp_path,
        evals_run=[BenchmarkEval(id=1, name="eval", prompt="prompt", expected_output="", expectations=[])],
        model="gpt-5",
        executor="openai",
        selected_configs=["smith_skill", "github_mcp"],
    )

    assert benchmark["metadata"]["configurations_run"] == ["smith_skill", "github_mcp"]
    assert benchmark["run_summary"]["delta"] == {
        "pass_rate": "+0.50",
        "time_seconds": "+4.3",
        "tokens": "+56",
    }
    assert "| github_mcp | 0.50 | 8.00 | 400 |" in markdown
