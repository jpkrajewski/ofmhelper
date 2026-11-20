from guards.event import Guard, HookEvent, Verdict, deny, test_run_since

NAME = "test_lock"
EDITS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
WAIT = (
    "scripts/test.sh is running (since {since}): a half-written file under src/ or tests/ breaks "
    "its collection and the whole run ends NOT GREEN. Wait for it to end, or stop it first."
)


def check(event: HookEvent) -> Verdict | None:
    if not event.relative.startswith(("src/", "tests/")):
        return None
    since = test_run_since()
    return deny(NAME, WAIT.format(since=since)) if since else None


GUARD = Guard(name=NAME, events=frozenset({"PreToolUse"}), tools=EDITS, check=check)
