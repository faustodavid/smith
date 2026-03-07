from __future__ import annotations

import pytest

from tests.support import make_runtime_config


@pytest.fixture
def runtime_config():
    return make_runtime_config()
