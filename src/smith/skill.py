from __future__ import annotations

import filecmp
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

DEFAULT_SKILL_TARGET = Path.home() / ".agents" / "skills" / "smith"
_SKILL_RELATIVE_PATH = Path("share") / "smith" / "skills" / "smith"
SKILL_MARKER_NAME = ".smith-skill-meta.json"
_FRESHNESS_STAMP_NAME = "skill-freshness-stamp"
_FRESHNESS_INTERVAL_SECONDS = 24 * 60 * 60
_FRESHNESS_EXEMPT_COMMANDS = {"skill.sync", "skill.status", "config.init"}
_STALE_SKILL_HINT = "run 'smith skill sync' to refresh."


@dataclass(frozen=True)
class SkillSyncResult:
    ok: bool
    status: str
    target: Path
    source: Path | None
    mode: str | None
    message: str

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "status": self.status,
            "target": str(self.target),
            "source": str(self.source) if self.source is not None else None,
            "mode": self.mode,
            "message": self.message,
        }


def default_skill_target_dir() -> Path:
    configured = os.getenv("SMITH_SKILL_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_SKILL_TARGET


def _path_from_env(name: str) -> Path | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    return Path(value).expanduser()


def _homebrew_prefix_from_path(path: Path) -> Path | None:
    parts = path.resolve().parts
    for index, part in enumerate(parts):
        if part != "Cellar":
            continue
        if index + 1 < len(parts) and parts[index + 1] == "smith":
            return Path(*parts[:index])
    return None


def _homebrew_skill_sources() -> list[Path]:
    prefixes: list[Path] = []
    configured_prefix = _path_from_env("SMITH_HOMEBREW_PREFIX")
    if configured_prefix is not None:
        prefixes.append(configured_prefix)

    for raw_path in [sys.argv[0], sys.executable, __file__]:
        try:
            prefix = _homebrew_prefix_from_path(Path(raw_path))
        except (OSError, RuntimeError):
            continue
        if prefix is not None and prefix not in prefixes:
            prefixes.append(prefix)

    # The opt/smith symlink always points at the active install (including
    # HEAD installs), so it is the only candidate that survives upgrades.
    return [prefix / "opt" / "smith" / _SKILL_RELATIVE_PATH for prefix in prefixes]


def _repo_skill_source() -> Path:
    return Path(__file__).resolve().parents[2] / "skills" / "smith"


def resolve_skill_source_dir() -> Path | None:
    configured = _path_from_env("SMITH_SKILL_SOURCE_DIR")
    if configured is not None:
        return configured if configured.exists() else None

    for candidate in [*_homebrew_skill_sources(), _repo_skill_source()]:
        if candidate.exists():
            return candidate
    return None


def _absolute_path_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _symlink_destination(target: Path) -> Path | None:
    try:
        destination = Path(os.readlink(target))
    except OSError:
        return None
    if destination.is_absolute():
        return _absolute_path_without_resolving(destination)
    return _absolute_path_without_resolving(target.parent / destination)


def _installed_version() -> str | None:
    try:
        return version("smith")
    except PackageNotFoundError:
        return None


def _write_sync_marker(target: Path, source: Path) -> None:
    marker = {
        "source": str(source),
        "version": _installed_version(),
        "synced_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    (target / SKILL_MARKER_NAME).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")


def _read_sync_marker(target: Path) -> dict[str, object] | None:
    try:
        data = json.loads((target / SKILL_MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _directory_contents_match(target: Path, source: Path) -> bool:
    if not target.is_dir() or not source.is_dir():
        return False

    marker = Path(SKILL_MARKER_NAME)
    target_entries = {path.relative_to(target) for path in target.rglob("*")} - {marker}
    source_entries = {path.relative_to(source) for path in source.rglob("*")} - {marker}
    if target_entries != source_entries:
        return False

    for relative_path in target_entries:
        target_path = target / relative_path
        source_path = source / relative_path
        if target_path.is_symlink() or source_path.is_symlink():
            if not target_path.is_symlink() or not source_path.is_symlink():
                return False
            if os.readlink(target_path) != os.readlink(source_path):
                return False
        elif target_path.is_dir() or source_path.is_dir():
            if not target_path.is_dir() or not source_path.is_dir():
                return False
        elif target_path.is_file() or source_path.is_file():
            if not target_path.is_file() or not source_path.is_file():
                return False
            if not filecmp.cmp(target_path, source_path, shallow=False):
                return False
        else:
            return False
    return True


def skill_target_points_to_source(target: Path, source: Path) -> bool:
    source = _absolute_path_without_resolving(source)
    try:
        if target.is_symlink():
            return _symlink_destination(target) == source
        if not target.exists():
            return False
        return target.resolve() == source.resolve() or _directory_contents_match(target, source)
    except OSError:
        return False


def sync_skill(
    *,
    source_dir: Path | None = None,
    target_dir: Path | None = None,
    prefer_symlink: bool = True,
) -> SkillSyncResult:
    target = target_dir or default_skill_target_dir()
    source = source_dir or resolve_skill_source_dir()

    if source is None or not source.exists():
        return SkillSyncResult(
            ok=False,
            status="missing_source",
            target=target,
            source=source,
            mode=None,
            message="Smith skill source not found.",
        )

    source = _absolute_path_without_resolving(source)
    target_parent = target.parent

    if target.exists() or target.is_symlink():
        if skill_target_points_to_source(target, source):
            mode = "symlink" if target.is_symlink() else "directory"
            return SkillSyncResult(
                ok=True,
                status="current",
                target=target,
                source=source,
                mode=mode,
                message=f"Smith skill already points to: {source}",
            )

    target_parent.mkdir(parents=True, exist_ok=True)

    mode = _stage_and_swap(source, target, prefer_symlink=prefer_symlink)
    if mode == "symlink":
        return SkillSyncResult(
            ok=True,
            status="linked",
            target=target,
            source=source,
            mode="symlink",
            message=f"Smith skill linked to: {target}",
        )
    return SkillSyncResult(
        ok=True,
        status="copied",
        target=target,
        source=source,
        mode="copy",
        message=f"Smith skill copied to: {target}",
    )


def _stage_and_swap(source: Path, target: Path, *, prefer_symlink: bool) -> str:
    """Build the new skill next to the target, then swap it in. The previous
    install is only displaced once the replacement is complete, and is restored
    if the swap fails."""
    temp_root = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent))
    staged = temp_root / "staged"
    backup = temp_root / "backup"
    mode: str | None = None
    try:
        if prefer_symlink:
            try:
                staged.symlink_to(source, target_is_directory=True)
                mode = "symlink"
            except OSError:
                pass
        if mode is None:
            shutil.copytree(source, staged)
            _write_sync_marker(staged, source)
            mode = "copy"
        if target.exists() or target.is_symlink():
            target.replace(backup)
        staged.replace(target)
    except Exception:
        if not (target.exists() or target.is_symlink()) and (backup.exists() or backup.is_symlink()):
            backup.replace(target)
        raise
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
    return mode


def _freshness_stamp_path() -> Path:
    cache_root = _path_from_env("XDG_CACHE_HOME") or Path.home() / ".cache"
    return cache_root / "smith" / _FRESHNESS_STAMP_NAME


def _freshness_check_due(stamp: Path) -> bool:
    try:
        age = time.time() - stamp.stat().st_mtime
    except OSError:
        return True
    return age >= _FRESHNESS_INTERVAL_SECONDS or age < 0


def _refresh_or_hint(target: Path, source: Path | None) -> None:
    result = None
    if source is not None:
        try:
            result = sync_skill(source_dir=source, target_dir=target)
        except Exception:
            result = None
    if result is not None and result.ok:
        print(f"smith: refreshed agent skill at {target}", file=sys.stderr)
    else:
        print(f"smith: agent skill at {target} may be out of date; {_STALE_SKILL_HINT}", file=sys.stderr)


def ensure_skill_fresh(command_id: str | None = None) -> None:
    """Refresh a stale skill install at most once a day. Must never break the CLI."""
    try:
        _ensure_skill_fresh(command_id)
    except Exception:  # pragma: no cover - defensive guard around CLI startup
        pass


def _ensure_skill_fresh(command_id: str | None) -> None:
    if os.getenv("SMITH_SKILL_CHECK", "").strip() == "0":
        return
    if command_id in _FRESHNESS_EXEMPT_COMMANDS:
        return

    target = default_skill_target_dir()
    # Only refresh installs that already exist; installing the skill is an
    # explicit choice made via `smith skill sync` or `smith config init`.
    if not target.exists() and not target.is_symlink():
        return

    stamp = _freshness_stamp_path()
    if not _freshness_check_due(stamp):
        return
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.touch()

    if target.is_symlink():
        # A working symlink is left alone even when it does not point at the
        # resolvable source: developers deliberately link to a repo checkout,
        # and Homebrew links track upgrades via the opt path on their own.
        if target.exists():
            return
        _refresh_or_hint(target, resolve_skill_source_dir())
        return

    marker = _read_sync_marker(target)
    source = resolve_skill_source_dir()
    if source is None and marker is not None:
        recorded = Path(str(marker.get("source") or "")).expanduser()
        if str(recorded) != "." and recorded.exists():
            source = recorded

    if source is not None:
        if skill_target_points_to_source(target, source):
            return
        if marker is None:
            # Without the sync marker the directory is not known to be
            # smith-managed; never delete content smith did not write.
            print(f"smith: agent skill at {target} may be out of date; {_STALE_SKILL_HINT}", file=sys.stderr)
            return
        _refresh_or_hint(target, source)
        return

    if marker is not None and marker.get("version") not in (None, _installed_version()):
        print(f"smith: agent skill at {target} may be out of date; {_STALE_SKILL_HINT}", file=sys.stderr)
