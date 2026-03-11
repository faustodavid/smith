from __future__ import annotations

import pytest

from smith.benchmark.smith_cli import build_smith_cli_subprocess, validate_smith_cli_command


def test_validate_smith_cli_command_injects_github_provider_for_code_search():
    tokens = validate_smith_cli_command('code search "otelcol.exporter.loki" --take 5')

    assert tokens == ["code", "search", "otelcol.exporter.loki", "--take", "5", "--provider", "github"]


def test_validate_smith_cli_command_rejects_non_github_grep():
    with pytest.raises(ValueError):
        validate_smith_cli_command("code grep azdo SRE repo pattern")


def test_build_smith_cli_subprocess_injects_grafana_org_and_local_src_path():
    argv, env = build_smith_cli_subprocess("repos github")

    assert argv[:3] == [argv[0], "-m", "smith.cli.main"]
    assert argv[3:] == ["repos", "github"]
    assert env["GITHUB_ORG"] == "grafana"
    assert "src" in env["PYTHONPATH"]
