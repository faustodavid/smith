from __future__ import annotations

import os
from pathlib import Path

from smith import skill


def _make_skill_source(tmp_path: Path) -> Path:
    source = tmp_path / "source" / "skills" / "smith"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: smith\n---\n", encoding="utf-8")
    return source


def _make_homebrew_opt_skill(tmp_path: Path) -> tuple[Path, Path]:
    cellar_prefix = tmp_path / "Cellar" / "smith" / "1.0"
    cellar_source = cellar_prefix / "share" / "smith" / "skills" / "smith"
    cellar_source.mkdir(parents=True)
    (cellar_source / "SKILL.md").write_text("---\nname: smith\n---\n", encoding="utf-8")

    opt_dir = tmp_path / "opt"
    opt_dir.mkdir()
    (opt_dir / "smith").symlink_to(cellar_prefix, target_is_directory=True)
    opt_source = opt_dir / "smith" / "share" / "smith" / "skills" / "smith"
    return opt_source, cellar_source


def test_sync_skill_links_target_to_source(tmp_path: Path) -> None:
    source = _make_skill_source(tmp_path)
    target = tmp_path / ".agents" / "skills" / "smith"

    result = skill.sync_skill(source_dir=source, target_dir=target)

    assert result.ok is True
    assert result.status == "linked"
    assert result.mode == "symlink"
    assert target.is_symlink()
    assert target.resolve() == source.resolve()


def test_sync_skill_replaces_stale_directory_with_symlink(tmp_path: Path) -> None:
    source = _make_skill_source(tmp_path)
    target = tmp_path / ".agents" / "skills" / "smith"
    target.mkdir(parents=True)
    (target / "old.md").write_text("old", encoding="utf-8")

    result = skill.sync_skill(source_dir=source, target_dir=target)

    assert result.ok is True
    assert result.status == "linked"
    assert target.is_symlink()
    assert target.resolve() == source.resolve()
    assert not (target / "old.md").exists()


def test_sync_skill_reports_current_when_copied_target_matches_source(tmp_path: Path) -> None:
    source = _make_skill_source(tmp_path)
    target = tmp_path / ".agents" / "skills" / "smith"

    copied = skill.sync_skill(source_dir=source, target_dir=target, prefer_symlink=False)
    current = skill.sync_skill(source_dir=source, target_dir=target, prefer_symlink=False)

    assert copied.ok is True
    assert copied.status == "copied"
    assert current.ok is True
    assert current.status == "current"
    assert current.mode == "directory"
    assert skill.skill_target_points_to_source(target, source) is True


def test_sync_skill_refreshes_stale_copied_target(tmp_path: Path) -> None:
    source = _make_skill_source(tmp_path)
    target = tmp_path / ".agents" / "skills" / "smith"
    skill.sync_skill(source_dir=source, target_dir=target, prefer_symlink=False)
    (source / "SKILL.md").write_text("---\nname: smith\nupdated: true\n---\n", encoding="utf-8")

    result = skill.sync_skill(source_dir=source, target_dir=target, prefer_symlink=False)

    assert result.ok is True
    assert result.status == "copied"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == (source / "SKILL.md").read_text(encoding="utf-8")


def test_sync_skill_reports_current_when_target_already_points_to_source(tmp_path: Path) -> None:
    source = _make_skill_source(tmp_path)
    target = tmp_path / ".agents" / "skills" / "smith"
    target.parent.mkdir(parents=True)
    target.symlink_to(source, target_is_directory=True)

    result = skill.sync_skill(source_dir=source, target_dir=target)

    assert result.ok is True
    assert result.status == "current"
    assert result.mode == "symlink"


def test_sync_skill_preserves_homebrew_opt_symlink_target(tmp_path: Path) -> None:
    opt_source, cellar_source = _make_homebrew_opt_skill(tmp_path)
    target = tmp_path / ".agents" / "skills" / "smith"

    result = skill.sync_skill(source_dir=opt_source, target_dir=target)

    assert result.ok is True
    assert result.status == "linked"
    assert result.source == opt_source
    assert target.is_symlink()
    assert Path(os.readlink(target)) == opt_source
    assert target.resolve() == cellar_source.resolve()


def test_sync_skill_replaces_cellar_link_with_opt_link(tmp_path: Path) -> None:
    opt_source, cellar_source = _make_homebrew_opt_skill(tmp_path)
    target = tmp_path / ".agents" / "skills" / "smith"
    target.parent.mkdir(parents=True)
    target.symlink_to(cellar_source, target_is_directory=True)

    result = skill.sync_skill(source_dir=opt_source, target_dir=target)

    assert result.ok is True
    assert result.status == "linked"
    assert result.source == opt_source
    assert target.is_symlink()
    assert Path(os.readlink(target)) == opt_source
    assert target.resolve() == cellar_source.resolve()


def test_sync_skill_reports_missing_source(tmp_path: Path) -> None:
    result = skill.sync_skill(source_dir=tmp_path / "missing", target_dir=tmp_path / "target")

    assert result.ok is False
    assert result.status == "missing_source"


def test_resolve_skill_source_prefers_homebrew_opt_path(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "opt" / "smith" / "share" / "smith" / "skills" / "smith"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: smith\n---\n", encoding="utf-8")
    monkeypatch.setenv("SMITH_HOMEBREW_PREFIX", str(tmp_path))
    monkeypatch.delenv("SMITH_SKILL_SOURCE_DIR", raising=False)

    assert skill.resolve_skill_source_dir() == source
