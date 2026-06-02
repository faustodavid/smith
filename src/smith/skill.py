from __future__ import annotations

import filecmp
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SKILL_TARGET = Path.home() / ".agents" / "skills" / "smith"
_SKILL_RELATIVE_PATH = Path("share") / "smith" / "skills" / "smith"


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

    candidates: list[Path] = []
    for prefix in prefixes:
        candidates.append(prefix / "opt" / "smith" / _SKILL_RELATIVE_PATH)
        candidates.append(prefix / "Cellar" / "smith" / "HEAD" / _SKILL_RELATIVE_PATH)
    return candidates


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


def _remove_existing_target(target: Path) -> None:
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.exists():
        shutil.rmtree(target)


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


def _directory_contents_match(target: Path, source: Path) -> bool:
    if not target.is_dir() or not source.is_dir():
        return False

    target_entries = {path.relative_to(target) for path in target.rglob("*")}
    source_entries = {path.relative_to(source) for path in source.rglob("*")}
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
        _remove_existing_target(target)

    target_parent.mkdir(parents=True, exist_ok=True)

    if prefer_symlink:
        try:
            target.symlink_to(source, target_is_directory=True)
            return SkillSyncResult(
                ok=True,
                status="linked",
                target=target,
                source=source,
                mode="symlink",
                message=f"Smith skill linked to: {target}",
            )
        except OSError:
            pass

    shutil.copytree(source, target)
    return SkillSyncResult(
        ok=True,
        status="copied",
        target=target,
        source=source,
        mode="copy",
        message=f"Smith skill copied to: {target}",
    )
