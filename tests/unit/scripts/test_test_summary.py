# Copyright (C) 2026 jpkrajewski

import importlib.util
import json
lazy from pathlib import Path

from repo_paths import REPO

_spec = importlib.util.spec_from_file_location(
    "test_summary",
    REPO / "scripts/test_summary.py",
)
assert _spec is not None
assert _spec.loader is not None
test_summary = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(test_summary)


def _test(nodeid: str, outcome: str, seconds: float) -> dict:
    call: dict = {"outcome": outcome, "duration": seconds}
    if outcome == "failed":
        call["crash"] = {"message": f"AssertionError: {nodeid} broke"}
    return {"nodeid": nodeid, "outcome": outcome, "call": call}


def _report(folder: Path, name: str, exitcode: int, tests: list[dict]) -> str:
    summary: dict[str, int] = {}
    for test in tests:
        summary[test["outcome"]] = summary.get(test["outcome"], 0) + 1
    path = folder / name
    report = {"exitcode": exitcode, "summary": summary, "tests": tests}
    path.write_text(json.dumps(report), encoding="utf-8")
    return str(path)


def test_two_runs_give_one_verdict_with_the_failures_and_slowest_of_both(
    tmp_path,
    capsys,
):
    ordinary = _report(
        tmp_path,
        "report.json",
        0,
        [_test("tests/unit/test_a.py::test_a", "passed", 0.5)],
    )
    browser = _report(
        tmp_path,
        "report-e2e.json",
        1,
        [
            _test("tests/e2e/test_b.py::test_b", "passed", 9.0),
            _test("tests/e2e/test_c.py::test_c", "failed", 2.0),
        ],
    )

    verdict = test_summary.main([ordinary, browser])

    assert verdict == 1
    assert capsys.readouterr().out.splitlines() == [
        (
            "SUMMARY exit 1: 2 passed, 1 failed, 0 error, 0 skipped, 0 xfailed,"
            " 0 not collected"
        ),
        (
            "FAILED tests/e2e/test_c.py::test_c (call):"
            " AssertionError: tests/e2e/test_c.py::test_c broke"
        ),
        "SLOWEST 10:",
        "     9.0s tests/e2e/test_b.py::test_b",
        "     2.0s tests/e2e/test_c.py::test_c",
        "     0.5s tests/unit/test_a.py::test_a",
        "RESULT NOT GREEN",
    ]


def test_a_run_that_collected_nothing_leaves_the_verdict_to_the_other(
    tmp_path,
    capsys,
):
    ordinary = _report(
        tmp_path,
        "report.json",
        0,
        [_test("tests/unit/test_a.py::test_a", "passed", 0.5)],
    )
    browser = _report(tmp_path, "report-e2e.json", 5, [])

    verdict = test_summary.main([ordinary, browser])

    output = capsys.readouterr().out
    assert verdict == 0
    assert output.startswith("SUMMARY exit 0: 1 passed,")
    assert output.endswith("RESULT GREEN\n")


def test_a_bare_full_run_whose_half_collected_nothing_is_not_green(tmp_path, capsys):
    ordinary = _report(
        tmp_path,
        "report.json",
        0,
        [_test("tests/unit/test_a.py::test_a", "passed", 0.5)],
    )
    browser = _report(tmp_path, "report-e2e.json", 5, [])

    verdict = test_summary.main(["--every-run-collects", ordinary, browser])

    output = capsys.readouterr().out
    assert verdict == 1
    assert output.startswith("SUMMARY exit 5: 1 passed,")
    assert output.endswith("RESULT NOT GREEN\n")


def test_nothing_collected_anywhere_is_not_green(tmp_path, capsys):
    ordinary = _report(tmp_path, "report.json", 5, [])
    browser = _report(tmp_path, "report-e2e.json", 5, [])

    verdict = test_summary.main([ordinary, browser])

    output = capsys.readouterr().out
    assert verdict == 1
    assert output.startswith("SUMMARY exit 5: 0 passed,")
    assert output.endswith("RESULT NOT GREEN\n")
