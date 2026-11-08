"""The app starts without the heavy libraries it only needs inside a job
(`lazy import`, Python 3.15); a worker loads them once, before it forks, so no
job pays for them. A fresh interpreter per check: the test session has already
imported half of them."""

import os
import subprocess
import sys

import pytest
from repo_paths import REPO

from ofmhelpers.worker import _LAZY_HEAVY

HEAVY = ("google.genai", "googleapiclient", "openpyxl", "yt_dlp", "imagehash")


def _loaded_after(code: str) -> set[str]:
    probe = f"import sys\n{code}\nprint(*[m for m in {HEAVY!r} if m in sys.modules])"
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO,
        # os.environ: the suite's bytecode cache, or every run compiles it all (20 s).
        env={**os.environ, "PYTHONPATH": str(REPO / "src"), "SESSION_SECRET": "x" * 32},
        capture_output=True,
        text=True,
        check=True,
    )
    return set(result.stdout.split())


@pytest.mark.slow
def test_starting_the_web_app_loads_none_of_them():
    assert _loaded_after("import ofmhelpers.web.main") == set()


def test_the_worker_preloads_what_jobs_need():
    assert {"google.genai", "yt_dlp", "numpy"} <= set(_LAZY_HEAVY)


def test_the_image_ships_compiled_bytecode():
    """Nothing writes bytecode at runtime (PYTHONDONTWRITEBYTECODE): without
    these every process start compiles ~1800 modules, ~2.5 s."""
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "--no-dev --compile-bytecode" in dockerfile
    assert "python -m compileall -q /app/src" in dockerfile
