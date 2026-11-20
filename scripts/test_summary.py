import json
import sys
from pathlib import Path

SLOWEST = 10
PHASES = ("setup", "call", "teardown")


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


def main(report_path: str) -> int:
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    summary = report.get("summary", {})
    tests = report.get("tests", [])
    not_collected = [
        c for c in report.get("collectors", []) if c.get("outcome") == "failed"
    ]
    broken = [t for t in tests if t.get("outcome") in ("failed", "error")]
    counts = ", ".join(
        f"{summary.get(key, 0)} {key}"
        for key in ("passed", "failed", "error", "skipped", "xfailed")
    )
    print(
        f"SUMMARY exit {report.get('exitcode')}: {counts}, {len(not_collected)} not collected"
    )
    for collector in not_collected:
        print(
            f"NOT COLLECTED {collector.get('nodeid')}: {last_line(collector.get('longrepr', ''))}"
        )
    for test in broken:
        phase, text = failure_text(test)
        print(f"{test['outcome'].upper()} {test['nodeid']} ({phase}): {text}")
    print(f"SLOWEST {SLOWEST}:")
    for test in sorted(tests, key=duration, reverse=True)[:SLOWEST]:
        print(f"  {duration(test):6.1f}s {test['nodeid']}")
    clean = report.get("exitcode") == 0 and not not_collected and not broken
    print("RESULT", "GREEN" if clean else "NOT GREEN")
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
