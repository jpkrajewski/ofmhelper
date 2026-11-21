from pathlib import Path

from guards.event import ROOT, Guard, HookEvent, Verdict, deny, owner_said_ship
from guards.shell import Statement, program_name, shell_statements

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
PIPED_PUSH = (
    "Never pipe git push through tail, head or grep: the pipe hides the pre-push verdict and "
    "its exit code. Write the push log to a file (git push origin dev > reports/loops/push.log "
    "2>&1, run_in_background) and read that file."
)
READ_ONLY_STASH = ("list", "show")
LOG_FILTERS = {"tail", "head", "grep", "rg", "select-object", "select-string"}


def subcommand(statement: Statement) -> list[str]:
    words = statement.words[1:]
    index = 0
    while index < len(words) and words[index].startswith("-"):
        index += 2 if words[index] in VALUE_FLAGS else 1
    return words[index:]


def piped_to_a_filter(statement: Statement) -> bool:
    return any(program_name(words[0]) in LOG_FILTERS for words in statement.piped_to)


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
    action, *rest = subcommand(statement) or [""]
    if action == "stash" and next(iter(rest), "") not in READ_ONLY_STASH:
        return deny(NAME, STASH)
    if action in OWNER_ONLY or (action == "reset" and "--hard" in statement.words):
        if not owner_said_ship(event):
            return deny(NAME, NOT_YOURS)
        if action == "push" and piped_to_a_filter(statement):
            return deny(NAME, PIPED_PUSH)
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
