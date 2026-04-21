"""Shared helpers for provider local-checkout grep paths.

These helpers implement:

- Partial + sparse clone optimization (compute_sparse_patterns,
  apply_sparse_patterns, read/write_sparse_state).
- `ls-remote` precheck to skip `git fetch` when HEAD matches (remote_head_sha,
  local_head_sha).
- Ripgrep primary path for code grep on a local checkout
  (require_ripgrep, ripgrep_local_result).

Ripgrep is a **hard runtime dependency** for local-checkout grep. Missing the
`rg` binary raises `SmithError` with an install hint. The check is a pure
`shutil.which` call: Smith never downloads or auto-installs ripgrep.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from typing import Any, Callable, Literal

from smith.errors import SmithError
from smith.providers.helpers import build_grep_result, grep_too_many_files_result
from smith.utils import normalize_path

logger = logging.getLogger(__name__)


GitSubprocess = Callable[[list[str]], None]
GitSubprocessOutput = Callable[[list[str]], str]


RIPGREP_INSTALL_HINT = (
    "ripgrep (`rg`) is required for local-checkout grep. "
    "Install from your OS package manager:\n"
    "  macOS:   brew install ripgrep\n"
    "  Debian:  sudo apt install ripgrep\n"
    "  Arch:    sudo pacman -S ripgrep\n"
    "  Windows: winget install BurntSushi.ripgrep.MSVC\n"
    "  Cargo:   cargo install ripgrep\n"
    "See https://github.com/BurntSushi/ripgrep#installation for more options."
)


def require_ripgrep() -> str:
    """Return the path to the `rg` binary or raise `SmithError`.

    Presence check only: we never download, vendor, or auto-install ripgrep.
    """
    path = shutil.which("rg")
    if path is None:
        raise SmithError(RIPGREP_INSTALL_HINT)
    return path


def compute_sparse_patterns(path: str | None, glob: str | None) -> list[str] | None:
    """Return sparse-checkout patterns narrowing the working tree.

    Only filename-only globs (no '/', '\\', '{', '}') are translated. Anything
    more complex falls back to a full checkout and is later filtered by
    ripgrep's own `--glob`. Returns `None` when no narrowing is possible.
    """
    normalized_path = ""
    if path:
        candidate = normalize_path(path).strip("/")
        if candidate:
            normalized_path = candidate

    glob_ok = bool(glob) and all(token not in (glob or "") for token in ("/", "\\", "{", "}"))
    effective_glob = glob if glob_ok else None

    if not normalized_path and not effective_glob:
        return None

    patterns: list[str] = ["/*"]
    if normalized_path and effective_glob:
        patterns.append(f"/{normalized_path}/**/{effective_glob}")
    elif normalized_path:
        patterns.append(f"/{normalized_path}/")
    elif effective_glob:
        patterns.append(f"**/{effective_glob}")
    return patterns


def sparse_state_marker(checkout_dir: str) -> str:
    return os.path.join(checkout_dir, ".git", "smith-sparse-patterns")


def read_sparse_state(checkout_dir: str) -> str | None:
    marker = sparse_state_marker(checkout_dir)
    try:
        with open(marker, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def write_sparse_state(checkout_dir: str, state: str) -> None:
    marker = sparse_state_marker(checkout_dir)
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write(state)
    except OSError as exc:
        logger.debug("Could not persist sparse state for %s: %s", checkout_dir, exc)


def apply_sparse_patterns(
    run_git: GitSubprocess,
    checkout_dir: str,
    patterns: list[str] | None,
) -> None:
    desired_state = "\n".join(patterns) if patterns is not None else ""
    current_state = read_sparse_state(checkout_dir)
    if current_state == desired_state:
        return
    if patterns is None and current_state is None:
        return
    try:
        if patterns is None:
            run_git(["git", "-C", checkout_dir, "sparse-checkout", "disable"])
        else:
            run_git(
                ["git", "-C", checkout_dir, "sparse-checkout", "set", "--no-cone", *patterns]
            )
        write_sparse_state(checkout_dir, desired_state)
    except Exception as exc:
        logger.debug("Could not apply sparse-checkout %s to %s: %s", patterns, checkout_dir, exc)


def remote_head_sha(
    run_git_output: GitSubprocessOutput,
    checkout_dir: str,
    branch: str,
) -> str | None:
    try:
        output = run_git_output(
            ["git", "-C", checkout_dir, "ls-remote", "origin", branch],
        )
    except Exception as exc:
        logger.debug("ls-remote failed for %s@%s: %s", checkout_dir, branch, exc)
        return None
    for line in output.splitlines():
        sha, *_ = line.strip().split()
        if sha:
            return sha
    return None


def local_head_sha(
    run_git_output: GitSubprocessOutput,
    checkout_dir: str,
) -> str | None:
    for candidate in ("FETCH_HEAD", "HEAD"):
        try:
            output = run_git_output(
                ["git", "-C", checkout_dir, "rev-parse", candidate],
            )
        except Exception:
            continue
        sha = output.strip()
        if sha:
            return sha
    return None


def is_path_within_checkout(path: str, checkout_root: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), checkout_root]) == checkout_root
    except ValueError:
        return False


def is_internal_local_path(path: str) -> bool:
    normalized = path.strip().lstrip("/").replace("\\", "/")
    return normalized == ".git" or normalized.startswith(".git/")


def ripgrep_local_result(
    *,
    checkout_dir: str,
    pattern: str,
    case_insensitive: bool,
    path: str | None,
    glob: str | None,
    filename_filter: re.Pattern[str],
    output_mode: Literal["content", "files_with_matches", "count"],
    context_lines: int,
    reverse: bool,
    max_output_chars: int,
    grep_max_files: int,
    truncation_hint: str,
) -> dict[str, Any]:
    """Run ripgrep against a local checkout and return a grep result dict.

    Raises `SmithError` when `rg` is missing.
    """
    rg_binary = require_ripgrep()

    checkout_root = os.path.realpath(checkout_dir)
    normalized_path = normalize_path(path)
    prefix = normalized_path.strip("/")

    def _empty() -> dict[str, Any]:
        return build_grep_result(
            output_lines=[],
            matched_count=0,
            warnings=[],
            max_output_chars=max_output_chars,
            truncation_hint=truncation_hint,
        )

    if prefix:
        target = os.path.join(checkout_dir, prefix.replace("/", os.sep))
        if not is_path_within_checkout(target, checkout_root) or os.path.islink(target):
            return _empty()
        if is_internal_local_path(prefix):
            return _empty()
        if not os.path.exists(target):
            return _empty()

    base_args = [
        rg_binary,
        "--no-messages",
        "--no-config",
        "--no-ignore",
        "--hidden",
        "--glob",
        "!.git",
    ]
    if case_insensitive:
        base_args.append("-i")
    if glob:
        base_args.extend(["--glob", glob])
    if output_mode == "files_with_matches":
        base_args.extend(["-l", "--sort", "path", "-e", pattern])
    elif output_mode == "count":
        base_args.extend(["-c", "--sort", "path", "-e", pattern])
    else:
        base_args.extend(["-n", "--heading", "--sort", "path"])
        if context_lines > 0:
            base_args.extend(["-C", str(context_lines)])
        base_args.extend(["-e", pattern])

    search_target = os.path.join(checkout_dir, prefix) if prefix else checkout_dir

    try:
        result = subprocess.run(
            [*base_args, search_target],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        raise SmithError(f"ripgrep failed to execute for {checkout_dir}: {exc}") from exc

    return_code = int(getattr(result, "returncode", 2))
    if return_code not in (0, 1):
        stderr = str(getattr(result, "stderr", "") or "").strip()
        raise SmithError(
            f"ripgrep exited with status {return_code} for {checkout_dir}: {stderr or 'unknown error'}"
        )

    stdout_text = str(getattr(result, "stdout", "") or "")

    def _to_relative(raw: str) -> str | None:
        raw = raw.strip()
        if not raw:
            return None
        if os.path.isabs(raw):
            try:
                rel = os.path.relpath(raw, checkout_dir)
            except ValueError:
                return None
        else:
            rel = raw
        rel = rel.replace(os.sep, "/").lstrip("./")
        if not rel or rel.startswith("../") or is_internal_local_path(rel):
            return None
        if not filename_filter.search(os.path.basename(rel)):
            return None
        return rel

    if output_mode == "files_with_matches":
        matched_paths: list[str] = []
        seen: set[str] = set()
        for line in stdout_text.splitlines():
            rel = _to_relative(line)
            if rel is None:
                continue
            full_path = f"/{rel}"
            if full_path in seen:
                continue
            seen.add(full_path)
            matched_paths.append(full_path)
        if len(matched_paths) > grep_max_files:
            return grep_too_many_files_result(len(matched_paths), grep_max_files)
        if reverse:
            matched_paths.reverse()
        return build_grep_result(
            output_lines=matched_paths,
            matched_count=len(matched_paths),
            warnings=[],
            max_output_chars=max_output_chars,
            truncation_hint=truncation_hint,
        )

    if output_mode == "count":
        count_lines: list[str] = []
        files_matched = 0
        for line in stdout_text.splitlines():
            raw_path, separator, raw_count = line.rpartition(":")
            if separator != ":" or not raw_count.isdigit():
                continue
            rel = _to_relative(raw_path)
            if rel is None:
                continue
            count_lines.append(f"/{rel}:{raw_count}")
            files_matched += 1
        if files_matched > grep_max_files:
            return grep_too_many_files_result(files_matched, grep_max_files)
        if reverse:
            count_lines.reverse()
        return build_grep_result(
            output_lines=count_lines,
            matched_count=files_matched,
            warnings=[],
            max_output_chars=max_output_chars,
            truncation_hint=truncation_hint,
        )

    file_blocks: list[tuple[str, list[list[str]]]] = []
    current_file: str | None = None
    current_block: list[str] = []
    files_matched = 0

    def _flush_block() -> None:
        nonlocal current_block
        if current_file is None:
            current_block = []
            return
        if not current_block:
            return
        file_blocks[-1][1].append(current_block)
        current_block = []

    for line in stdout_text.splitlines():
        if line == "--":
            _flush_block()
            continue
        if not line:
            _flush_block()
            current_file = None
            continue

        match = re.match(r"^(\d+)([:-])(.*)$", line)
        if match and current_file is not None:
            current_block.append(f"{match.group(1)}{match.group(2)}{match.group(3)}")
            continue

        rel = _to_relative(line)
        if rel is not None:
            _flush_block()
            current_file = f"/{rel}"
            file_blocks.append((current_file, []))
            files_matched += 1
            continue

        _flush_block()
        current_file = None

    _flush_block()

    if files_matched > grep_max_files:
        return grep_too_many_files_result(files_matched, grep_max_files)

    ordered_blocks: list[tuple[str, list[list[str]]]]
    if reverse:
        ordered_blocks = [
            (file_path, list(reversed(blocks))) for file_path, blocks in reversed(file_blocks)
        ]
    else:
        ordered_blocks = file_blocks

    output_lines: list[str] = []
    for file_path, blocks in ordered_blocks:
        output_lines.append(file_path)
        for block_index, block in enumerate(blocks):
            if block_index > 0:
                output_lines.append("--")
            output_lines.extend(block)

    return build_grep_result(
        output_lines=output_lines,
        matched_count=files_matched,
        warnings=[],
        max_output_chars=max_output_chars,
        truncation_hint=truncation_hint,
    )
