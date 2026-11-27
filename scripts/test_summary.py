# Copyright (C) 2026 jpkrajewski

import json
import sys
from pathlib import Path

import pytest

SLOWEST = 10
PHASES = ("setup", "call", "teardown")
COUNTED = ("passed", "failed", "error", "skipped", "xfailed")
EVERY_RUN_COLLECTS = "--every-run-collects"


def last_line(text: str) -> str:
    lines = [line for line in (text or "").strip().splitlines() if line.strip()]
    return lines[-1].strip()[:300] if lines else ""


def first_line(text: str) -> str:
    lines = [line for line in (text or "").strip().splitlines() if line.strip()]
    return lines[0].strip()[:300] if lines else ""


def failure_text(test: dict) -> tuple[str, str]:
    for phase in PHASES:
        result = test.get(phase) or {}
        if result.get("outcome") not in (None, "passed", "skipped"):
            crash = (result.get("crash") or {}).get("message", "")
            return phase, first_line(crash) or last_line(result.get("longrepr", ""))
    return "", ""


def duration(test: dict) -> float:
    return sum((test.get(phase) or {}).get("duration", 0) for phase in PHASES)


def read_report(report_path: str) -> dict:
    return json.loads(Path(report_path).read_text(encoding="utf-8"))


def not_collected_file(collector: dict) -> bool:
    return collector.get("outcome") == "failed"


def report_path(argument: str) -> bool:
    return argument != EVERY_RUN_COLLECTS


def broken_test(test: dict) -> bool:
    return test.get("outcome") in ("failed", "error")


class Reports:
    def __init__(self, report_paths: list[str], *, every_run_collects: bool) -> None:
        self.reports: list[dict] = list(map(read_report, report_paths))
        self.every_run_collects = every_run_collects

    def tests(self) -> list[dict]:
        found: list[dict] = []
        for report in self.reports:
            found.extend(report.get("tests", []))
        return found

    def not_collected(self) -> list[dict]:
        found: list[dict] = []
        for report in self.reports:
            found.extend(filter(not_collected_file, report.get("collectors", [])))
        return found

    def counted(self, outcome: str) -> str:
        total = 0
        for report in self.reports:
            total += report.get("summary", {}).get(outcome, 0)
        return f"{total} {outcome}"

    def exit_code(self) -> int:
        found = pytest.ExitCode.NO_TESTS_COLLECTED
        for report in self.reports:
            code = report.get("exitcode")
            if code == pytest.ExitCode.NO_TESTS_COLLECTED:
                if self.every_run_collects:
                    return pytest.ExitCode.NO_TESTS_COLLECTED
                continue
            if found in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED):
                found = code
        return found


def main(arguments: list[str]) -> int:
    reports = Reports(
        list(filter(report_path, arguments)),
        every_run_collects=EVERY_RUN_COLLECTS in arguments,
    )
    tests = reports.tests()
    not_collected = reports.not_collected()
    broken = list(filter(broken_test, tests))
    exitcode = reports.exit_code()
    print(
        f"SUMMARY exit {exitcode}: "
        f"{', '.join(map(reports.counted, COUNTED))}, "
        f"{len(not_collected)} not collected",
    )
    for collector in not_collected:
        print(
            f"NOT COLLECTED {collector.get('nodeid')}: "
            f"{last_line(collector.get('longrepr', ''))}",
        )
    for test in broken:
        phase, text = failure_text(test)
        print(f"{test['outcome'].upper()} {test['nodeid']} ({phase}): {text}")
    print(f"SLOWEST {SLOWEST}:")
    for test in sorted(tests, key=duration, reverse=True)[:SLOWEST]:
        print(f"  {duration(test):6.1f}s {test['nodeid']}")
    clean = exitcode == pytest.ExitCode.OK and not not_collected and not broken
    print("RESULT", "GREEN" if clean else "NOT GREEN")
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
