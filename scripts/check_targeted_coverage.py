#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

from coverage import Coverage


MODULE_FLOORS: dict[str, float] = {
    "smith.cli.main": 95.0,
    "smith.cli.handlers": 85.0,
    "smith.client": 80.0,
    "smith.formatting": 80.0,
    "smith.providers.base": 85.0,
    "smith.providers.github": 45.0,
    "smith.providers.azdo": 45.0,
}


def _module_path(repo_root: Path, module_name: str) -> Path:
    relative = Path("src").joinpath(*module_name.split(".")).with_suffix(".py")
    return repo_root / relative


def _coverage_for_file(cov: Coverage, file_path: Path) -> tuple[int, int, float]:
    _filename, statements, _excluded, missing, _formatted = cov.analysis2(str(file_path))
    total = len(statements)
    covered = total - len(missing)
    percent = 100.0 if total == 0 else (covered / total) * 100.0
    return covered, total, percent


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    data_file = repo_root / ".coverage"
    if not data_file.exists():
        print("Missing .coverage data. Run pytest with --cov before checking targeted coverage.", file=sys.stderr)
        return 2

    cov = Coverage(data_file=str(data_file))
    cov.load()

    failures: list[str] = []
    print("Targeted coverage:")
    for module_name, floor in MODULE_FLOORS.items():
        file_path = _module_path(repo_root, module_name)
        covered, total, percent = _coverage_for_file(cov, file_path)
        status = "OK" if percent >= floor else "FAIL"
        print(f"  {module_name:<24} {percent:6.2f}%  min {floor:5.2f}%  ({covered}/{total})  {status}")
        if percent < floor:
            failures.append(f"{module_name} is {percent:.2f}% but requires {floor:.2f}%")

    if failures:
        print("\nCoverage gate failures:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
