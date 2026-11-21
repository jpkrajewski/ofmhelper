import subprocess
import time

from guards.event import (
    ROOT,
    SHIP_OPEN,
    STATE,
    Guard,
    HookEvent,
    Markers,
    Verdict,
    context,
    deny,
    modified_at,
    owner_said_ship,
    test_run_since,
)
from guards.shell import Statement, shell_statements

NAME = "tests"
GATE_GREEN = STATE / "gate.green"
GIT_TIMEOUT_S = 5
OWNER_PRESETS = {"push", "all", "live", "deploy", "durations"}
FULL_SUITE = {"deploy", "all", "push"}
SHELLS = {"bash", "sh"}
ONLY_TEST_SH = (
    "CLAUDE.md Testing: tests run ONLY via scripts/test.sh [preset | test files] "
    "(Docker, a report every time), never pytest directly."
)
PIPED = (
    "CLAUDE.md Testing: never pipe test output through grep/head/tail. The verdict is "
    "reports/latest/summary.txt, the full log reports/latest/output.log."
)
SCOPED = (
    "CLAUDE.md Testing: while working run only the tests of what you touched: "
    "scripts/test.sh tests/path/test_x.py ... The full suite, push, all, e2e, live and deploy "
    "run in the ship skill, on the owner's word."
)
PAID = "Paid tests spend money at providers: only when the owner's own prompt asks for a paid run."
BUSY = (
    "A test run is already going (since {since}): there is one postgres-test and two runs clash. "
    "Wait for it to end, or stop it first."
)
REPORT = (
    "When this run ends, report from reports/latest/summary.txt: pass/fail/skip counts, "
    "each failure with its error text, the 10 slowest tests."
)
GREEN_ONCE = (
    "the gate is green at {at} on this exact tree (HEAD^{{tree}} {tree}): run scoped tests; "
    'pre-push and CI rerun the suite. Only the owner\'s prompt "gate" (the bare word) reruns it.'
)
TREE_UNKNOWN = (
    "the gate is green at {at}, and whether HEAD is still that tree is unknown "
    "(git rev-parse HEAD^{{tree}}: {error}), so the full suite is not rerun. Fix that first, "
    'or the owner\'s prompt "gate" (the bare word) reruns it.'
)


def gate_green_at() -> str:
    opened = modified_at(SHIP_OPEN)
    green = modified_at(GATE_GREEN)
    if not opened or green <= opened:
        return ""
    return time.strftime("%H:%M:%S", time.localtime(green))


def head_tree() -> str:
    done = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD^{tree}"],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or f"git exited {done.returncode}")
    return done.stdout.strip()


def green_tree() -> str:
    words = GATE_GREEN.read_text(encoding="utf-8").split()
    return words[0] if words else ""


def rerun_refusal(at: str) -> Verdict | None:
    try:
        tree = head_tree()
        same = tree == green_tree()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return deny(NAME, TREE_UNKNOWN.format(at=at, error=str(exc)[:200]))
    return deny(NAME, GREEN_ONCE.format(at=at, tree=tree[:12])) if same else None


def test_sh_args(statement: Statement) -> list[str] | None:
    words = statement.words[1:] if statement.program in SHELLS else statement.words
    if words and words[0].replace("\\", "/").endswith("scripts/test.sh"):
        return words[1:]
    return None


def runs_pytest(statement: Statement) -> bool:
    return statement.program == "pytest" or (
        statement.program == "docker" and "pytest" in statement.words
    )


def needs_owner(args: list[str]) -> bool:
    return not args or args[0] in OWNER_PRESETS or args == ["e2e"]


def judge(statement: Statement, event: HookEvent) -> Verdict | None:
    args = test_sh_args(statement)
    if args is None:
        return deny(NAME, ONLY_TEST_SH) if runs_pytest(statement) else None
    if statement.piped_to:
        return deny(NAME, PIPED)
    if args[:1] == ["paid"] and not Markers.of(event).has("owner-paid"):
        return deny(NAME, PAID)
    if needs_owner(args) and not owner_said_ship(event):
        return deny(NAME, SCOPED)
    if (
        args[:1]
        and args[0] in FULL_SUITE
        and not Markers.of(event).has("owner-gate")
        and (at := gate_green_at())
        and (refused := rerun_refusal(at))
    ):
        return refused
    if since := test_run_since():
        return deny(NAME, BUSY.format(since=since))
    return context(NAME, REPORT)


def check(event: HookEvent) -> Verdict | None:
    return next(
        (verdict for s in shell_statements(event) if (verdict := judge(s, event))),
        None,
    )


GUARD = Guard(
    name=NAME,
    events=frozenset({"PreToolUse"}),
    tools=frozenset({"Bash", "PowerShell"}),
    check=check,
)
