from __future__ import annotations

import io
import os
import shlex
import subprocess
import sys
import threading
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Iterator

from smith.benchmark.constants import BENCHMARK_GITHUB_ORG
from smith.cli.handlers import (
    EXIT_API_FAILURE,
    EXIT_AUTH_FAILURE,
    EXIT_INVALID_ARGS,
    _emit_error,
    validate_args_for_provider,
)
from smith.cli.parser import build_parser
from smith.client import SmithClient
from smith.errors import SmithApiError, SmithAuthError

REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_RUNNER_LOCK = threading.Lock()
_DEFAULT_RUNNER: InProcessSmithCliRunner | None = None


def _strip_benchmark_global_flags(tokens: list[str]) -> list[str]:
    root_commands = {"code", "repos", "orgs"}
    ignored_flags = {"--github-org", "--azdo-org", "--format"}
    ignored_switches = {"--verbose", "-v"}
    output: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in root_commands:
            output.extend(tokens[index:])
            break
        if token in ignored_switches:
            index += 1
            continue
        if token in ignored_flags:
            index += 2
            continue
        if token.startswith("--github-org=") or token.startswith("--azdo-org=") or token.startswith("--format="):
            index += 1
            continue
        output.extend(tokens[index:])
        break
    return output


def validate_smith_cli_command(command: str) -> list[str]:
    tokens = shlex.split(command)
    if not tokens:
        raise ValueError("smith_cli requires a non-empty command.")
    if tokens[0] == "smith":
        tokens = tokens[1:]
    tokens = _strip_benchmark_global_flags(tokens)
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


@contextmanager
def _patched_environment(env: dict[str, str]) -> Iterator[None]:
    original = os.environ.copy()
    try:
        os.environ.clear()
        os.environ.update(env)
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


class InProcessSmithCliRunner:
    def __init__(
        self,
        *,
        repo_root: Path = REPO_ROOT,
        env: dict[str, str] | None = None,
    ) -> None:
        self._repo_root = repo_root
        self._env_overrides = dict(env or {})
        self._parser = build_parser()
        self._client: SmithClient | None = None
        self._lock = threading.RLock()
        self._success_cache: dict[tuple[tuple[str, ...], tuple[tuple[str, str], ...]], str] = {}

    def _build_run_env(self, *, env: dict[str, str] | None = None) -> dict[str, str]:
        run_env = os.environ.copy()
        run_env.update(self._env_overrides)
        if env:
            run_env.update(env)
        run_env["GITHUB_ORG"] = BENCHMARK_GITHUB_ORG
        run_env["PYTHONPATH"] = build_smith_pythonpath(
            repo_root=self._repo_root,
            existing_pythonpath=run_env.get("PYTHONPATH"),
        )
        return run_env

    def _get_or_create_client(self, args: Any) -> SmithClient:
        if self._client is None:
            self._client = SmithClient(
                azdo_org=getattr(args, "azdo_org", None),
                github_org=getattr(args, "github_org", None),
            )
        return self._client

    def _run_once(self, tokens: list[str], *, run_env: dict[str, str]) -> tuple[int, str, str]:
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        with _patched_environment(run_env):
            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                try:
                    args = self._parser.parse_args(tokens)
                except SystemExit as exc:
                    code = int(exc.code) if exc.code is not None else 0
                else:
                    handler = getattr(args, "handler", None)
                    if handler is None:
                        self._parser.print_help()
                        code = EXIT_INVALID_ARGS
                    else:
                        command_id = str(getattr(args, "command_id", "unknown"))
                        try:
                            validate_args_for_provider(args)
                            client = self._get_or_create_client(args)
                            code = int(handler(client, args))
                        except ValueError as exc:
                            code = _emit_error(
                                args=args,
                                command=command_id,
                                code="invalid_args",
                                message=str(exc),
                                exit_code=EXIT_INVALID_ARGS,
                            )
                        except SmithAuthError as exc:
                            code = _emit_error(
                                args=args,
                                command=command_id,
                                code="auth_failure",
                                message=str(exc),
                                exit_code=EXIT_AUTH_FAILURE,
                            )
                        except SmithApiError as exc:
                            code = _emit_error(
                                args=args,
                                command=command_id,
                                code="api_error",
                                message=str(exc),
                                exit_code=EXIT_API_FAILURE,
                            )
                        except Exception as exc:  # pragma: no cover - defensive fallback
                            code = _emit_error(
                                args=args,
                                command=command_id,
                                code="unexpected_error",
                                message=f"Unexpected error: {exc}",
                                exit_code=EXIT_API_FAILURE,
                            )

        return code, stdout_buffer.getvalue().strip(), stderr_buffer.getvalue().strip()

    def execute(
        self,
        command: str,
        *,
        env: dict[str, str] | None = None,
    ) -> str:
        tokens = validate_smith_cli_command(command)
        cache_key = (tuple(tokens), tuple(sorted((env or {}).items())))
        if cache_key in self._success_cache:
            return self._success_cache[cache_key]
        run_env = self._build_run_env(env=env)
        with self._lock:
            cached = self._success_cache.get(cache_key)
            if cached is not None:
                return cached
            code, stdout, stderr = self._run_once(tokens, run_env=run_env)
            if code != 0:
                details = stderr or stdout or f"smith exited with code {code}"
                raise RuntimeError(details)
            result = stdout or "(no output)"
            self._success_cache[cache_key] = result
            return result


def _default_runner() -> InProcessSmithCliRunner:
    global _DEFAULT_RUNNER
    if _DEFAULT_RUNNER is not None:
        return _DEFAULT_RUNNER
    with _DEFAULT_RUNNER_LOCK:
        if _DEFAULT_RUNNER is None:
            _DEFAULT_RUNNER = InProcessSmithCliRunner()
    return _DEFAULT_RUNNER


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
    run_env["GITHUB_ORG"] = BENCHMARK_GITHUB_ORG
    run_env["PYTHONPATH"] = build_smith_pythonpath(
        repo_root=repo_root,
        existing_pythonpath=run_env.get("PYTHONPATH"),
    )
    return [sys.executable, "-m", "smith.cli.main", *tokens], run_env


def execute_smith_cli_subprocess(
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


def execute_smith_cli_command(
    command: str,
    *,
    repo_root: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
    runner: InProcessSmithCliRunner | None = None,
) -> str:
    if runner is not None:
        return runner.execute(command, env=env)
    if env is not None or repo_root != REPO_ROOT:
        return InProcessSmithCliRunner(repo_root=repo_root, env=env).execute(command)
    return _default_runner().execute(command)
