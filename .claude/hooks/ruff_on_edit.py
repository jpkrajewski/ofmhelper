"""Claude Code PostToolUse hook: `ruff format` + safe `ruff check --fix` on
the one .py file an Edit/Write just touched, so "would reformat" never
reaches pre-commit or CI.

Runs the host .venv's ruff (the lockfile's version), like the pre-commit
hooks already do: a container start per edit would cost seconds. Never
removes code that is unused *yet*: an import added one edit before its
first use stays (F401, F841, ERA001 not fixed). Always exits 0; a failure
is a note on stderr, never a blocked edit.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_KEEP = "F401,F841,ERA001"


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    path = Path(event.get("tool_input", {}).get("file_path", ""))
    if path.suffix != ".py" or not path.is_file():
        return
    try:
        path.resolve().relative_to(ROOT)
    except ValueError:
        return  # not this repo's file: its own ruff config may differ
    ruff = [sys.executable, "-m", "ruff"]
    for args in (
        ["format", "--quiet", str(path)],
        ["check", "--fix", "--quiet", "--unfixable", _KEEP, "--exit-zero", str(path)],
    ):
        result = subprocess.run(
            [*ruff, *args], cwd=ROOT, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            print(
                f"ruff_on_edit: ruff {args[0]}: {result.stderr.strip()}",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()
