#!/usr/bin/env python3
"""Cross-platform installer for Smith. Works on macOS and Windows."""

import shutil
import subprocess
import sys
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


def sync_skill(source: Path, target: Path) -> None:
    """Copy skill directory to target."""
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
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
        if REPO_DIR.exists():
            shutil.rmtree(REPO_DIR)
        run(["git", "clone", REPO_URL, str(REPO_DIR)])

    if not SKILL_SOURCE.exists():
        print(f"Error: skill directory not found after install: {SKILL_SOURCE}", file=sys.stderr)
        sys.exit(1)

    print("==> Syncing skill")
    sync_skill(SKILL_SOURCE, TARGET_SKILL_DIR)

    print("==> Installing smith CLI globally with uv")
    run(["uv", "tool", "install", "-e", str(REPO_DIR), "--force"])

    print("==> Ensuring smith is on PATH")
    run(["uv", "tool", "update-shell"])

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
