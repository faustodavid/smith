#!/usr/bin/env python3
"""Cross-platform installer for Smith. Works on macOS and Windows."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_URL = "https://github.com/faustodavid/smith.git"
REPO_DIR = Path.home() / ".local/share/smith"
SKILL_SOURCE = REPO_DIR / "skills/smith"
TARGET_SKILL_DIR = Path.home() / ".agents/skills/smith"


def run(cmd: list[str], **kwargs) -> None:
    """Run a command, exiting on failure."""
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        sys.exit(result.returncode)


def require_tool(name: str, install_hint: str) -> None:
    """Exit if a required tool is not found."""
    if not shutil.which(name):
        print(f"Error: {name} is not installed.")
        print(install_hint)
        sys.exit(1)


def find_smith_executable() -> str | None:
    """Locate the smith CLI, including fresh installs where uv's bin dir is not on PATH yet."""
    found = shutil.which("smith")
    if found:
        return found
    try:
        result = subprocess.run(["uv", "tool", "dir", "--bin"], capture_output=True, text=True)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    name = "smith.exe" if sys.platform == "win32" else "smith"
    candidate = Path(result.stdout.strip()) / name
    if candidate.exists():
        return str(candidate)
    return None


def sync_skill_via_cli(source: Path) -> bool:
    """Sync the skill with the installed CLI so all install paths share one implementation."""
    smith_bin = find_smith_executable()
    if not smith_bin:
        return False
    env = dict(os.environ)
    env["SMITH_SKILL_SOURCE_DIR"] = str(source)
    result = subprocess.run([smith_bin, "skill", "sync"], env=env)
    return result.returncode == 0


def sync_skill(source: Path, target: Path) -> None:
    """Copy skill directory to target. Fallback for when the smith CLI is not on PATH."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent))
    staged = temp_root / "staged"
    backup = temp_root / "backup"
    try:
        shutil.copytree(source, staged)
        if target.exists():
            target.replace(backup)
        staged.replace(target)
    except Exception:
        if backup.exists() and not target.exists():
            backup.replace(target)
        raise
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
    print(f"  Synced skill to: {target}")


def main() -> None:
    print("==> Smith installer")

    require_tool("uv", "Install uv from: https://github.com/astral-sh/uv\n  curl -LsSf https://astral.sh/uv/install.sh | sh")
    require_tool("git", "Install git from: https://git-scm.com/")
    require_tool(
        "rg",
        "Install ripgrep from your OS package manager:\n"
        "  macOS:   brew install ripgrep\n"
        "  Debian:  sudo apt install ripgrep\n"
        "  Arch:    sudo pacman -S ripgrep\n"
        "  Windows: winget install BurntSushi.ripgrep.MSVC\n"
        "  Cargo:   cargo install ripgrep\n"
        "See https://github.com/BurntSushi/ripgrep#installation for more options.",
    )

    REPO_DIR.parent.mkdir(parents=True, exist_ok=True)
    TARGET_SKILL_DIR.parent.mkdir(parents=True, exist_ok=True)

    if (REPO_DIR / ".git").exists():
        print(f"==> Updating repo checkout at {REPO_DIR}")
        run(["git", "-C", str(REPO_DIR), "pull", "--ff-only", "origin", "main"])
    else:
        print(f"==> Cloning smith to {REPO_DIR}")
        if REPO_DIR.exists() and any(REPO_DIR.iterdir()):
            print(f"Error: refusing to replace non-git directory: {REPO_DIR}", file=sys.stderr)
            print("Move or remove it before installing Smith.", file=sys.stderr)
            sys.exit(1)
        run(["git", "clone", REPO_URL, str(REPO_DIR)])

    if not SKILL_SOURCE.exists():
        print(f"Error: skill directory not found after install: {SKILL_SOURCE}", file=sys.stderr)
        sys.exit(1)

    print("==> Installing smith CLI globally with uv")
    run(["uv", "tool", "install", "-e", str(REPO_DIR), "--force"])

    print("==> Ensuring smith is on PATH")
    run(["uv", "tool", "update-shell"])

    print("==> Syncing skill")
    if not sync_skill_via_cli(SKILL_SOURCE):
        target = Path(os.environ.get("SMITH_SKILL_DIR") or TARGET_SKILL_DIR).expanduser()
        sync_skill(SKILL_SOURCE, target)

    print()
    print("Smith installed successfully!")
    print()
    print(f"  Repo:     {REPO_DIR}")
    print(f"  Skill:    {TARGET_SKILL_DIR}")
    print("  CLI:      smith")
    update_cmd = "python" if sys.platform == "win32" else "python3"
    print(f"  Update:   {update_cmd} {REPO_DIR / 'scripts/install.py'}")
    print()
    print("Try: smith --help")


if __name__ == "__main__":
    main()
