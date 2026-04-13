from __future__ import annotations

import io
import os
import shlex
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

from smith.benchmark.constants import BENCHMARK_GITHUB_ORG
from smith.cli.handlers import (
    EXIT_API_FAILURE,
    EXIT_AUTH_FAILURE,
    EXIT_INVALID_ARGS,
    _emit_error,
    validate_args_for_remote,
)
from smith.cli.parser import build_parser
from smith.client import SmithClient
from smith.config import RemoteConfig, SmithConfig, load_config, save_config
from smith.errors import SmithApiError, SmithAuthError

REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_RUNNER_LOCK = threading.Lock()
_DEFAULT_RUNNER: InProcessSmithCliRunner | None = None


def _strip_benchmark_global_flags(tokens: list[str]) -> list[str]:
    ignored_flags = {"--format"}
    ignored_switches = {"--verbose", "-v"}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in ignored_switches:
            index += 1
            continue
        if token in ignored_flags:
            index += 2
            continue
        if token.startswith("--format="):
            index += 1
            continue
        break
    return tokens[index:]


def _benchmark_github_remote_name(*, env: dict[str, str] | None = None) -> str:
    resolved_env = os.environ.copy()
    if env:
        resolved_env.update(env)

    config_path_value = str(resolved_env.get("SMITH_CONFIG", "") or "").strip()
    if not config_path_value:
        return "github"

    config = load_config(config_path=Path(config_path_value).expanduser())
    for remote in config.remotes.values():
        if remote.provider == "github" and remote.enabled:
            return remote.name
    raise ValueError("smith_cli requires an enabled GitHub remote in SMITH_CONFIG.")


def _has_cli_flag(tokens: list[str], flag: str) -> bool:
    return any(token == flag or token.startswith(f"{flag}=") for token in tokens)


def validate_smith_cli_command(command: str, *, env: dict[str, str] | None = None) -> list[str]:
    tokens = shlex.split(command)
    if not tokens:
        raise ValueError("smith_cli requires a non-empty command.")
    if tokens[0] == "smith":
        tokens = tokens[1:]
    tokens = _strip_benchmark_global_flags(tokens)
    if not tokens:
        raise ValueError("smith_cli requires Smith subcommand arguments after `smith`.")

    github_remote = _benchmark_github_remote_name(env=env)

    if len(tokens) >= 2 and tokens[:2] == ["code", "search"]:
        if _has_cli_flag(tokens, "--remote"):
            raise ValueError("smith_cli does not allow `--remote` with `code search`. Use `<remote> code search`.")
        if _has_cli_flag(tokens, "--project"):
            raise ValueError("smith_cli does not allow `--project` with `code search`. Use `<remote> code search`.")
        if _has_cli_flag(tokens, "--repo"):
            raise ValueError("smith_cli does not allow `--repo` with `code search`. Use `<remote> code search`.")
        return tokens

    if len(tokens) >= 3 and tokens[:3] == [github_remote, "code", "search"]:
        return tokens

    if len(tokens) >= 3 and tokens[:3] == [github_remote, "code", "grep"]:
        return tokens

    if tokens[:2] == [github_remote, "repos"]:
        return tokens

    if tokens[:2] == [github_remote, "orgs"]:
        return tokens

    allowed = ", ".join([
        "`code search`",
        f"`{github_remote} code search`",
        f"`{github_remote} code grep`",
        f"`{github_remote} repos`",
        f"`{github_remote} orgs`",
    ])
    raise ValueError(f"smith_cli only allows: {allowed}.")


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


def _benchmark_smith_config(*, api_url: str | None = None) -> SmithConfig:
    resolved_api_url = (api_url or "https://api.github.com").strip().rstrip("/")
    parsed = urlparse(resolved_api_url)
    host = parsed.netloc or "github.com"
    return SmithConfig(
        remotes={
            "github": RemoteConfig(
                name="github",
                provider="github",
                org=BENCHMARK_GITHUB_ORG,
                host=host,
                token_env="GITHUB_TOKEN",
                enabled=True,
                api_url=resolved_api_url,
            )
        },
        defaults={},
    )


def _benchmark_config_path(*, run_env: dict[str, str]) -> Path:
    api_url = run_env.get("GITHUB_API_URL")
    config_key = abs(hash((BENCHMARK_GITHUB_ORG, api_url or "")))
    path = Path(tempfile.gettempdir()) / f"smith-benchmark-{config_key}.yaml"
    save_config(_benchmark_smith_config(api_url=api_url), config_path=path)
    return path


class InProcessSmithCliRunner:
    def __init__(
        self,
        *,
        repo_root: Path = REPO_ROOT,
        env: dict[str, str] | None = None,
    ) -> None:
        self._repo_root = repo_root
        self._env_overrides = dict(env or {})
        self._client: SmithClient | None = None
        self._client_key: tuple[str, ...] | None = None
        self._lock = threading.RLock()
        self._success_cache: dict[tuple[tuple[str, ...], tuple[tuple[str, str], ...], str], str] = {}

    def _build_run_env(self, *, env: dict[str, str] | None = None) -> dict[str, str]:
        run_env = os.environ.copy()
        run_env.update(self._env_overrides)
        if env:
            run_env.update(env)
        if not str(run_env.get("SMITH_CONFIG", "") or "").strip():
            run_env["SMITH_CONFIG"] = str(_benchmark_config_path(run_env=run_env))
        run_env["PYTHONPATH"] = build_smith_pythonpath(
            repo_root=self._repo_root,
            existing_pythonpath=run_env.get("PYTHONPATH"),
        )
        return run_env

    def _get_or_create_client(self, run_env: dict[str, str]) -> SmithClient:
        client_key = (
            (run_env.get("GITHUB_API_URL") or "").strip().rstrip("/"),
            run_env.get("SMITH_CONFIG", ""),
        )
        if self._client is None or self._client_key != client_key:
            self._client = SmithClient()
            self._client_key = client_key
        return self._client

    def _run_once(self, tokens: list[str], *, run_env: dict[str, str]) -> tuple[int, str, str]:
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        with _patched_environment(run_env):
            parser = build_parser()
            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                try:
                    args = parser.parse_args(tokens)
                except SystemExit as exc:
                    code = int(exc.code) if exc.code is not None else 0
                else:
                    handler = getattr(args, "handler", None)
                    if handler is None:
                        parser.print_help()
                        code = EXIT_INVALID_ARGS
                    else:
                        command_id = str(getattr(args, "command_id", "unknown"))
                        try:
                            validate_args_for_remote(args)
                            client = self._get_or_create_client(run_env)
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
        run_env = self._build_run_env(env=env)
        tokens = validate_smith_cli_command(command, env=run_env)
        cache_key = (tuple(tokens), tuple(sorted((env or {}).items())), run_env.get("SMITH_CONFIG", ""))
        if cache_key in self._success_cache:
            return self._success_cache[cache_key]
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
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    if not str(run_env.get("SMITH_CONFIG", "") or "").strip():
        run_env["SMITH_CONFIG"] = str(_benchmark_config_path(run_env=run_env))
    tokens = validate_smith_cli_command(command, env=run_env)
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
