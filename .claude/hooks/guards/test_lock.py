# Copyright (C) 2026 jpkrajewski

import subprocess

from guards.event import (
    ROOT,
    TEST_LOCK,
    Guard,
    HookEvent,
    Verdict,
    deny,
    test_run_since,
)

NAME = "test_lock"
EDITS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
GIT_TIMEOUT_S = 5
WAIT = (
    "scripts/test.sh is running (since {since}): a "
    "half-written file under src/ or tests/ breaks "
    "its collection and the whole run ends NOT "
    "GREEN. Wait for it to end, or stop it first."
)
PUSH_WAIT = (
    "The pre-push suite is running (since {since}): "
    "pre-commit refuses the push when a tracked "
    "file changes during its run, so {path} waits. "
    "Edit only untracked paths (new files, "
    "reports/) until it ends."
)


def running_preset() -> str:
    try:
        first = TEST_LOCK.read_text(encoding="utf-8").split(maxsplit=1)
    except FileNotFoundError:  # silent: the run ended meanwhile
        return ""
    return first[0] if first else ""


def tracked_by_git(relative: str) -> bool:
    try:
        done = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "--error-unmatch", "--", relative],
            capture_output=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (
        OSError,
        subprocess.TimeoutExpired,
    ):  # silent: unknown = tracked, the edit waits
        return True
    return done.returncode == 0


def check(event: HookEvent) -> Verdict | None:
    if not event.relative:
        return None
    since = test_run_since()
    if not since:
        return None
    if running_preset() == "push":
        if not tracked_by_git(event.relative):
            return None
        return deny(NAME, PUSH_WAIT.format(since=since, path=event.relative))
    if event.relative.startswith(("src/", "tests/")):
        return deny(NAME, WAIT.format(since=since))
    return None


GUARD = Guard(name=NAME, events=frozenset({"PreToolUse"}), tools=EDITS, check=check)
