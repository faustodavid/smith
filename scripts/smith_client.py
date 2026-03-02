#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from smith.client import SmithApiError, SmithAuthError, SmithClient, SmithError  # noqa: E402

__all__ = ["SmithClient", "SmithError", "SmithAuthError", "SmithApiError"]
