from __future__ import annotations

import json
import os
import shutil
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


def test_sync_skill_copy_writes_marker_with_source_and_version(tmp_path: Path) -> None:
    source = _make_skill_source(tmp_path)
    target = tmp_path / ".agents" / "skills" / "smith"

    result = skill.sync_skill(source_dir=source, target_dir=target, prefer_symlink=False)

    assert result.status == "copied"
    marker = skill._read_sync_marker(target)
    assert marker is not None
    assert marker["source"] == str(source)
    assert marker["version"] == skill._installed_version()


def test_sync_skill_symlink_does_not_write_marker(tmp_path: Path) -> None:
    source = _make_skill_source(tmp_path)
    target = tmp_path / ".agents" / "skills" / "smith"

    result = skill.sync_skill(source_dir=source, target_dir=target)

    assert result.mode == "symlink"
    assert not (source / skill.SKILL_MARKER_NAME).exists()


def _freshness_env(monkeypatch, tmp_path: Path, target: Path, source: Path | None) -> None:
    monkeypatch.setenv("SMITH_SKILL_DIR", str(target))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("SMITH_SKILL_CHECK", raising=False)
    monkeypatch.delenv("SMITH_HOMEBREW_PREFIX", raising=False)
    if source is not None:
        monkeypatch.setenv("SMITH_SKILL_SOURCE_DIR", str(source))
    else:
        monkeypatch.setenv("SMITH_SKILL_SOURCE_DIR", str(tmp_path / "missing-source"))


def test_ensure_skill_fresh_refreshes_stale_copy(monkeypatch, capsys, tmp_path: Path) -> None:
    source = _make_skill_source(tmp_path)
    target = tmp_path / ".agents" / "skills" / "smith"
    skill.sync_skill(source_dir=source, target_dir=target, prefer_symlink=False)
    (source / "SKILL.md").write_text("---\nname: smith\nupdated: true\n---\n", encoding="utf-8")
    _freshness_env(monkeypatch, tmp_path, target, source)

    skill.ensure_skill_fresh("repos.list")

    assert "refreshed agent skill" in capsys.readouterr().err
    assert skill.skill_target_points_to_source(target, source)


def test_ensure_skill_fresh_repairs_broken_symlink(monkeypatch, capsys, tmp_path: Path) -> None:
    source = _make_skill_source(tmp_path)
    target = tmp_path / ".agents" / "skills" / "smith"
    target.parent.mkdir(parents=True)
    target.symlink_to(tmp_path / "gone", target_is_directory=True)
    _freshness_env(monkeypatch, tmp_path, target, source)

    skill.ensure_skill_fresh("repos.list")

    assert "refreshed agent skill" in capsys.readouterr().err
    assert skill.skill_target_points_to_source(target, source)


def test_ensure_skill_fresh_noop_for_current_symlink(monkeypatch, capsys, tmp_path: Path) -> None:
    source = _make_skill_source(tmp_path)
    target = tmp_path / ".agents" / "skills" / "smith"
    skill.sync_skill(source_dir=source, target_dir=target)
    _freshness_env(monkeypatch, tmp_path, target, source)

    skill.ensure_skill_fresh("repos.list")

    assert capsys.readouterr().err == ""


def test_ensure_skill_fresh_hints_on_version_mismatch_without_source(monkeypatch, capsys, tmp_path: Path) -> None:
    source = _make_skill_source(tmp_path)
    target = tmp_path / ".agents" / "skills" / "smith"
    skill.sync_skill(source_dir=source, target_dir=target, prefer_symlink=False)
    shutil.rmtree(source)
    marker_path = target / skill.SKILL_MARKER_NAME
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["version"] = "0.0.0"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    _freshness_env(monkeypatch, tmp_path, target, None)

    skill.ensure_skill_fresh("repos.list")

    assert "may be out of date" in capsys.readouterr().err
    assert not target.is_symlink()


def test_ensure_skill_fresh_uses_marker_source_when_unresolvable(monkeypatch, capsys, tmp_path: Path) -> None:
    source = _make_skill_source(tmp_path)
    target = tmp_path / ".agents" / "skills" / "smith"
    skill.sync_skill(source_dir=source, target_dir=target, prefer_symlink=False)
    (source / "SKILL.md").write_text("---\nname: smith\nupdated: true\n---\n", encoding="utf-8")
    _freshness_env(monkeypatch, tmp_path, target, None)

    skill.ensure_skill_fresh("repos.list")

    assert "refreshed agent skill" in capsys.readouterr().err
    assert skill.skill_target_points_to_source(target, source)


def test_ensure_skill_fresh_throttled_by_stamp(monkeypatch, capsys, tmp_path: Path) -> None:
    source = _make_skill_source(tmp_path)
    target = tmp_path / ".agents" / "skills" / "smith"
    skill.sync_skill(source_dir=source, target_dir=target, prefer_symlink=False)
    (source / "SKILL.md").write_text("---\nname: smith\nupdated: true\n---\n", encoding="utf-8")
    _freshness_env(monkeypatch, tmp_path, target, source)
    stamp = tmp_path / "cache" / "smith" / "skill-freshness-stamp"
    stamp.parent.mkdir(parents=True)
    stamp.touch()

    skill.ensure_skill_fresh("repos.list")

    assert capsys.readouterr().err == ""
    assert not skill.skill_target_points_to_source(target, source)


def test_ensure_skill_fresh_respects_kill_switch(monkeypatch, capsys, tmp_path: Path) -> None:
    source = _make_skill_source(tmp_path)
    target = tmp_path / ".agents" / "skills" / "smith"
    skill.sync_skill(source_dir=source, target_dir=target, prefer_symlink=False)
    (source / "SKILL.md").write_text("---\nname: smith\nupdated: true\n---\n", encoding="utf-8")
    _freshness_env(monkeypatch, tmp_path, target, source)
    monkeypatch.setenv("SMITH_SKILL_CHECK", "0")

    skill.ensure_skill_fresh("repos.list")

    assert capsys.readouterr().err == ""


def test_ensure_skill_fresh_skips_exempt_commands(monkeypatch, capsys, tmp_path: Path) -> None:
    source = _make_skill_source(tmp_path)
    target = tmp_path / ".agents" / "skills" / "smith"
    skill.sync_skill(source_dir=source, target_dir=target, prefer_symlink=False)
    (source / "SKILL.md").write_text("---\nname: smith\nupdated: true\n---\n", encoding="utf-8")
    _freshness_env(monkeypatch, tmp_path, target, source)

    skill.ensure_skill_fresh("skill.sync")

    assert capsys.readouterr().err == ""
    assert not skill.skill_target_points_to_source(target, source)


def test_ensure_skill_fresh_skips_missing_target(monkeypatch, capsys, tmp_path: Path) -> None:
    source = _make_skill_source(tmp_path)
    target = tmp_path / ".agents" / "skills" / "smith"
    _freshness_env(monkeypatch, tmp_path, target, source)

    skill.ensure_skill_fresh("repos.list")

    assert capsys.readouterr().err == ""
    assert not target.exists()


def test_ensure_skill_fresh_silent_for_current_copy(monkeypatch, capsys, tmp_path: Path) -> None:
    source = _make_skill_source(tmp_path)
    target = tmp_path / ".agents" / "skills" / "smith"
    skill.sync_skill(source_dir=source, target_dir=target, prefer_symlink=False)
    _freshness_env(monkeypatch, tmp_path, target, source)

    skill.ensure_skill_fresh("repos.list")

    assert capsys.readouterr().err == ""
    assert not target.is_symlink()


def test_ensure_skill_fresh_never_deletes_unmanaged_directory(monkeypatch, capsys, tmp_path: Path) -> None:
    source = _make_skill_source(tmp_path)
    target = tmp_path / ".agents" / "skills" / "smith"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("hand-rolled", encoding="utf-8")
    (target / "my-notes.txt").write_text("keep me", encoding="utf-8")
    _freshness_env(monkeypatch, tmp_path, target, source)

    skill.ensure_skill_fresh("repos.list")

    assert "may be out of date" in capsys.readouterr().err
    assert (target / "my-notes.txt").read_text(encoding="utf-8") == "keep me"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "hand-rolled"


def test_ensure_skill_fresh_leaves_working_symlink_to_other_source(monkeypatch, capsys, tmp_path: Path) -> None:
    source = _make_skill_source(tmp_path)
    dev_checkout = tmp_path / "dev" / "skills" / "smith"
    dev_checkout.mkdir(parents=True)
    (dev_checkout / "SKILL.md").write_text("dev copy", encoding="utf-8")
    target = tmp_path / ".agents" / "skills" / "smith"
    target.parent.mkdir(parents=True)
    target.symlink_to(dev_checkout, target_is_directory=True)
    _freshness_env(monkeypatch, tmp_path, target, source)

    skill.ensure_skill_fresh("repos.list")

    assert capsys.readouterr().err == ""
    assert Path(os.readlink(target)) == dev_checkout


def test_sync_skill_restores_previous_copy_when_staging_fails(monkeypatch, tmp_path: Path) -> None:
    source = _make_skill_source(tmp_path)
    target = tmp_path / ".agents" / "skills" / "smith"
    skill.sync_skill(source_dir=source, target_dir=target, prefer_symlink=False)
    original = (target / "SKILL.md").read_text(encoding="utf-8")
    (source / "SKILL.md").write_text("---\nname: smith\nupdated: true\n---\n", encoding="utf-8")

    def _failing_copytree(src, dst, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(skill.shutil, "copytree", _failing_copytree)

    try:
        skill.sync_skill(source_dir=source, target_dir=target, prefer_symlink=False)
    except OSError:
        pass

    assert (target / "SKILL.md").read_text(encoding="utf-8") == original
