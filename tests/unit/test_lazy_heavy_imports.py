import os
import subprocess
import sys

import pytest
from repo_paths import REPO

from ofmhelpers.background.worker import _LAZY_HEAVY

HEAVY = ("google.genai", "googleapiclient", "openpyxl", "yt_dlp", "imagehash")


def _loaded_after(code: str) -> set[str]:
    probe = f"import sys\n{code}\nprint(*[m for m in {HEAVY!r} if m in sys.modules])"
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO,
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
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "--no-dev --compile-bytecode" in dockerfile
    assert "python -m compileall -q /app/src" in dockerfile
