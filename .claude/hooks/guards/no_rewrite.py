import re

from guards.event import ROOT, Guard, HookEvent, Verdict, block, deny
from guards.shell_writes import SCRIPTED_WRITE, changed_by_this_call

NAME = "no_rewrite"
SHELLS = frozenset({"Bash", "PowerShell"})
IN_PLACE_EDIT = re.compile(r"\b(?:sed|perl)\s+(?:-\S+\s+)*(?:-\w*i|--in-place)")
USE_EDIT = (
    "{path} exists: change it with Edit, not Write. The ruff hook reformats a file after every "
    "write, so a second Write fails 'modified since read', and a whole-file rewrite hides what "
    "changed."
)
SHELL_REWRITE = (
    "{paths}: an existing .py was rewritten from the shell. Change an existing .py with Edit: "
    "the ruff hook formats it and the edit shows what changed. Check the rewrite now with "
    "git diff -- {paths}."
)


def rewritten_from_shell(event: HookEvent) -> list[str]:
    if not SCRIPTED_WRITE.search(event.command) or IN_PLACE_EDIT.search(event.command):
        return []
    return [
        f.path
        for f in changed_by_this_call(event)
        if not f.is_new and f.path.endswith(".py")
    ]


def after_shell(event: HookEvent) -> Verdict | None:
    if event.tool_name not in SHELLS:
        return None
    paths = rewritten_from_shell(event)
    return block(NAME, SHELL_REWRITE.format(paths=" ".join(paths))) if paths else None


def check(event: HookEvent) -> Verdict | None:
    if event.hook_event_name == "PostToolUse":
        return after_shell(event)
    path = event.relative
    if event.tool_name != "Write" or not path.endswith(".py"):
        return None
    return deny(NAME, USE_EDIT.format(path=path)) if (ROOT / path).is_file() else None


GUARD = Guard(
    name=NAME,
    events=frozenset({"PreToolUse", "PostToolUse"}),
    tools=frozenset({"Write", *SHELLS}),
    check=check,
)
