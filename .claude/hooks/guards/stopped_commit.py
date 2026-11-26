# Copyright (C) 2026 jpkrajewski

import os
import subprocess
import time
from pathlib import Path

from guards.event import ROOT, Guard, HookEvent, Verdict, block, modified_at

NAME = "stopped_commit"
FRESH_S = 7200
GIT_TIMEOUT_S = 4
STRANDED = (
    "A stopped run left pre-commit's copy of the "
    "unstaged changes in {patch}, and it still "
    "applies: those changes (another session's "
    "included) are missing from the working tree. "
    'Restore them now: git apply "{patch}", then git status.'
)


def pre_commit_home() -> Path:
    if home := os.environ.get("PRE_COMMIT_HOME"):
        return Path(home)
    cache = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(cache) / "pre-commit"


def newest_fresh_patch() -> Path | None:
    newest = max(pre_commit_home().glob("patch*"), key=modified_at, default=None)
    if newest is None or modified_at(newest) <= time.time() - FRESH_S:
        return None
    return newest


def still_applies(patch: Path) -> bool:
    try:
        done = subprocess.run(
            ["git", "-C", str(ROOT), "apply", "--check", str(patch)],
            capture_output=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:  # silent: unknown = nothing to restore
        return False
    return done.returncode == 0


def check(_event: HookEvent) -> Verdict | None:
    patch = newest_fresh_patch()
    if patch is None or not still_applies(patch):
        return None
    return block(NAME, STRANDED.format(patch=patch.as_posix()))


GUARD = Guard(
    name=NAME,
    events=frozenset({"PostToolUse"}),
    tools=frozenset({"TaskStop"}),
    check=check,
)
