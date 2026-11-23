# Copyright (C) 2026 jpkrajewski

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "ofmhelpers"
FIXTURES = REPO / "tests" / "fixtures"

assert (REPO / "pyproject.toml").is_file(), REPO
assert SRC.is_dir(), SRC
