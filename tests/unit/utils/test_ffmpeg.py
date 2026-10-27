"""The shared ffmpeg/ffprobe runner: a failure reports the tool's own last
lines, a missing binary is a FileNotFoundError."""

import sys

import pytest

from ofmhelpers.utils.ffmpeg import binary, run, run_bytes

FAILS = (
    "import sys; sys.stderr.write(chr(10).join('abc') + chr(10) + 'boom'); sys.exit(1)"
)


@pytest.mark.parametrize("runner", [run, run_bytes])
def test_failure_raises_with_the_tools_last_lines(runner):
    with pytest.raises(RuntimeError, match=r"failed: b / c / boom") as caught:
        runner([sys.executable, "-c", FAILS], 30)
    assert "sys.exit" not in str(caught.value)


@pytest.mark.parametrize("runner", [run, run_bytes])
def test_a_timeout_raises_naming_only_the_tool(runner):
    with pytest.raises(RuntimeError, match=r"timed out after 1 s") as caught:
        runner([sys.executable, "-c", "import time; time.sleep(5)"], 1)
    assert "time.sleep" not in str(caught.value)


def test_returns_stdout():
    assert run([sys.executable, "-c", "print('ok')"], 30).strip() == "ok"


def test_run_bytes_returns_stdout_as_bytes():
    script = "import sys; sys.stdout.buffer.write(bytes([0, 128, 255]))"
    assert run_bytes([sys.executable, "-c", script], 30) == bytes([0, 128, 255])


def test_missing_binary():
    with pytest.raises(FileNotFoundError):
        binary("definitely-not-a-real-binary-xyz")
