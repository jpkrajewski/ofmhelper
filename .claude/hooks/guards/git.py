from pathlib import Path

from guards.event import ROOT, Guard, HookEvent, Verdict, deny, owner_said_ship
from guards.shell import Statement, shell_statements

NAME = "git"
OWNER_ONLY = {"commit", "push"}
VALUE_FLAGS = {"-C", "-c", "--git-dir", "--work-tree"}
NOT_YOURS = (
    "CLAUDE.md Git: never commit, push or open/merge a PR on your own. Only the owner's word "
    "(ship, commit, push, deploy) does that, through the ship skill. Report what changed instead."
)
STASH = (
    "Never git stash here: a failed stash and a chained pop applied another branch's stash onto dev "
    "(2026-11-01). To prove old behaviour, revert the one line by hand, run, restore."
)
FOREGROUND_PUSH = (
    "git push runs the 3+ minute pre-push suite and times out in the foreground: run it with "
    "run_in_background, then compare git ls-remote origin dev with HEAD. Never push twice."
)


def subcommand(statement: Statement) -> str:
    words = statement.words[1:]
    index = 0
    while index < len(words) and words[index].startswith("-"):
        index += 2 if words[index] in VALUE_FLAGS else 1
    return words[index] if index < len(words) else ""


def outside_this_repo(statement: Statement, event: HookEvent) -> bool:
    words = statement.words
    target = Path(event.cwd or ROOT)
    if "-C" in words[:-1]:
        given = Path(words[words.index("-C") + 1])
        target = given if given.is_absolute() else target / given
    return not target.resolve().is_relative_to(ROOT.resolve())


def judge(statement: Statement, event: HookEvent) -> Verdict | None:
    if outside_this_repo(statement, event):
        return None
    if statement.program == "gh" and statement.words[1:3] in (
        ["pr", "create"],
        ["pr", "merge"],
    ):
        return None if owner_said_ship(event) else deny(NAME, NOT_YOURS)
    if statement.program != "git":
        return None
    action = subcommand(statement)
    if action == "stash":
        return deny(NAME, STASH)
    if action in OWNER_ONLY or (action == "reset" and "--hard" in statement.words):
        if not owner_said_ship(event):
            return deny(NAME, NOT_YOURS)
        if action == "push" and not event.in_background:
            return deny(NAME, FOREGROUND_PUSH)
    return None


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
