from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import make_runtime_config


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run live provider integration smoke tests.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "unit: fast isolated tests")
    config.addinivalue_line("markers", "contract: public behavior and boundary tests")
    config.addinivalue_line("markers", "integration: live provider smoke tests")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip_integration = pytest.mark.skip(reason="integration tests require --run-integration")
    root = Path(config.rootpath)

    for item in items:
        try:
            item_path = Path(str(item.path)).resolve().relative_to(root.resolve())
        except ValueError:
            continue

        parts = item_path.parts
        if len(parts) < 2 or parts[0] != "tests":
            continue

        if parts[1] == "unit":
            item.add_marker(pytest.mark.unit)
        elif parts[1] == "contract":
            item.add_marker(pytest.mark.contract)
        elif parts[1] == "integration":
            item.add_marker(pytest.mark.integration)
            if not config.getoption("--run-integration"):
                item.add_marker(skip_integration)


@pytest.fixture
def runtime_config():
    return make_runtime_config()
