from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def validate_smith_cli_command(command: str) -> list[str]:
    tokens = shlex.split(command)
    if not tokens:
        raise ValueError("smith_cli requires a non-empty command.")
    if tokens[0] == "smith":
        tokens = tokens[1:]
    if not tokens:
        raise ValueError("smith_cli requires Smith subcommand arguments after `smith`.")

    if tokens[:2] == ["code", "search"]:
        if "--project" in tokens:
            raise ValueError("smith_cli only supports GitHub code search for this benchmark.")
        if "--provider" in tokens:
            provider_index = tokens.index("--provider") + 1
            if provider_index >= len(tokens) or tokens[provider_index] != "github":
                raise ValueError("smith_cli only allows --provider github for code search.")
        else:
            tokens.extend(["--provider", "github"])
        return tokens

    if len(tokens) >= 3 and tokens[:3] == ["code", "grep", "github"]:
        return tokens

    if tokens[:2] == ["repos", "github"]:
        return tokens

    if tokens[:2] == ["orgs", "github"]:
        return tokens

    raise ValueError(
        "smith_cli only allows: `code search`, `code grep github`, `repos github`, and `orgs github`."
    )


def build_smith_pythonpath(
    *,
    repo_root: Path = REPO_ROOT,
    existing_pythonpath: str | None = None,
) -> str:
    src_path = str(repo_root / "src")
    if not existing_pythonpath:
        return src_path
    return f"{src_path}{os.pathsep}{existing_pythonpath}"


def build_smith_cli_subprocess(
    command: str,
    *,
    repo_root: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
) -> tuple[list[str], dict[str, str]]:
    tokens = validate_smith_cli_command(command)
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    run_env["GITHUB_ORG"] = "grafana"
    run_env["PYTHONPATH"] = build_smith_pythonpath(
        repo_root=repo_root,
        existing_pythonpath=run_env.get("PYTHONPATH"),
    )
    return [sys.executable, "-m", "smith.cli.main", *tokens], run_env


def execute_smith_cli_command(
    command: str,
    *,
    repo_root: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
) -> str:
    argv, run_env = build_smith_cli_subprocess(command, repo_root=repo_root, env=env)
    completed = subprocess.run(
        argv,
        cwd=str(repo_root),
        env=run_env,
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if completed.returncode != 0:
        details = stderr or stdout or f"smith exited with code {completed.returncode}"
        raise RuntimeError(details)
    return stdout or "(no output)"
