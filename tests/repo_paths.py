"""Where things are in the checkout, for tests that read the repo itself
(importable from any test: tests/ is on sys.path via its conftest)."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "ofmhelpers"
FIXTURES = REPO / "tests" / "fixtures"

assert (REPO / "pyproject.toml").is_file(), REPO
assert SRC.is_dir(), SRC
