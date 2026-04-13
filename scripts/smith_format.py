#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from smith.formatting import dumps_json, format_grep_matches, glob_to_regex, make_envelope, normalize_branch_name, render_text, truncate_output  # noqa: E402

__all__ = [
    "make_envelope",
    "dumps_json",
    "normalize_branch_name",
    "glob_to_regex",
    "format_grep_matches",
    "truncate_output",
    "render_text",
]
