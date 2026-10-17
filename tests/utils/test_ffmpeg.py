"""The shared ffmpeg/ffprobe runner: a failure reports the tool's own last
lines, a missing binary is a FileNotFoundError."""

import sys

import pytest

from ofmhelpers.utils.ffmpeg import binary, run


def test_failure_raises_with_the_tools_last_lines():
    script = "import sys; sys.stderr.write(chr(10).join('abc') + chr(10) + 'boom'); sys.exit(1)"
    with pytest.raises(RuntimeError, match=r"failed: b / c / boom"):
        run([sys.executable, "-c", script], 30)


def test_returns_stdout():
    assert run([sys.executable, "-c", "print('ok')"], 30).strip() == "ok"


def test_missing_binary():
    with pytest.raises(FileNotFoundError):
        binary("definitely-not-a-real-binary-xyz")
