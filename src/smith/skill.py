from __future__ import annotations

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

    source = source.resolve()
    target_parent = target.parent

    if target.exists() or target.is_symlink():
        try:
            if target.resolve() == source:
                mode = "symlink" if target.is_symlink() else "directory"
                return SkillSyncResult(
                    ok=True,
                    status="current",
                    target=target,
                    source=source,
                    mode=mode,
                    message=f"Smith skill already points to: {source}",
                )
        except OSError:
            pass
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
