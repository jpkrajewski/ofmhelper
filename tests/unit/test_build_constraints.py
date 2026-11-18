"""A package the lock holds only as source is compiled in every image build,
and uv does not lock the tools that build runs: each must be pinned exactly in
pyproject's `[tool.uv] build-constraint-dependencies`, or the production
builder runs whatever PyPI serves that minute (audit 2026-11-18)."""

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
# What each source-only package's own `build-system.requires` names, read from
# its locked sdist. A new version or a new source-only package: read its
# requires, pin each, then add it here.
BUILD_REQUIRES = {
    "pdqhash": ("0.2.8", {"setuptools", "wheel", "numpy", "cython"}),
    # No wheel for Python 3.15 yet (pre-commit and the test tools need it).
    "pyyaml": ("6.0.3", {"setuptools", "cython"}),
}


def _lock() -> dict:
    return tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))


def _source_only() -> dict[str, str]:
    return {
        package["name"]: package["version"]
        for package in _lock()["package"]
        if "sdist" in package and not package.get("wheels")
    }


def _pins() -> dict[str, str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pinned = project["tool"]["uv"]["build-constraint-dependencies"]
    return dict(pin.split("==") for pin in pinned)


def test_every_source_only_package_has_its_build_tools_read():
    assert _source_only() == {
        name: version for name, (version, _tools) in BUILD_REQUIRES.items()
    }


@pytest.mark.parametrize("name", sorted(BUILD_REQUIRES))
def test_its_build_tools_are_pinned_exactly(name):
    _version, tools = BUILD_REQUIRES[name]

    assert tools <= set(_pins())


def test_the_lock_holds_the_pins():
    held = _lock()["manifest"]["build-constraints"]

    assert {f"{c['name']}=={c['specifier'].removeprefix('==')}" for c in held} == {
        f"{name}=={version}" for name, version in _pins().items()
    }
