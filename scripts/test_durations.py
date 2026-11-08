"""Writes .test_durations (pytest-split's file: test id -> seconds) from the
last full run's reports/latest/report.json, so CI's three test groups take
about the same time.

pytest-split's own `--store-durations` reads the terminal reporter's stats,
which an xdist worker does not have; scripts/test.sh already writes every
test's setup/call/teardown time into report.json, so that is the source.

    scripts/test.sh durations     (a `push` run, then this)
    python scripts/test_durations.py reports/<run>/report.json   (an older run)
"""

import json
import sys
from pathlib import Path

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/latest/report.json"
OUT = ROOT / ".test_durations"
# A full `push` run collects ~6,300; fewer means a partial run.
MIN_TESTS = 5000
# What a test costs a CI runner beyond its time here: fresh tables, slower disk
# and CPU. Without it the group of 4297 quick tests took 6 m 16 s and the group
# of 1603 slow ones 3 m 20 s, with equal seconds on paper (CI 2026-11-08):
# (376 s - 200 s) / (4297 - 1603).
CI_OVERHEAD_S = 0.065


class Phase(BaseModel):
    duration: float = 0.0


class Test(BaseModel):
    nodeid: str
    setup: Phase = Phase()
    call: Phase = Phase()
    teardown: Phase = Phase()


class Report(BaseModel):
    tests: list[Test]


def main() -> int:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else REPORT
    if not source.is_file():
        print(f"test_durations: no {source}")
        return 1
    report = Report.model_validate_json(source.read_text(encoding="utf-8"))
    if len(report.tests) < MIN_TESTS:
        print(f"test_durations: {len(report.tests)} tests in {source}: not a full run")
        return 1
    durations = {
        t.nodeid: round(
            t.setup.duration + t.call.duration + t.teardown.duration + CI_OVERHEAD_S, 3
        )
        for t in report.tests
    }
    OUT.write_text(
        json.dumps(dict(sorted(durations.items())), indent=0) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"test_durations: {len(durations)} tests -> {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
