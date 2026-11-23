# Copyright (C) 2026 jpkrajewski

import re
from pathlib import Path

from guards.event import ROOT, Guard, HookEvent, Verdict, block, deny
from guards.shell import HEREDOC, program_name, shell_statements, statements
from guards.shell_writes import SCRIPTED_WRITE, changed_by_this_call

NAME = "no_rewrite"
SHELLS = frozenset({"Bash", "PowerShell"})
IN_PLACE_EDIT = re.compile(r"\b(?:sed|perl)\s+(?:-\S+\s+)*(?:-\w*i|--in-place)")
SAFE_PROGRAMS = frozenset({"git", "ruff", "test.sh"})
READ_PROGRAMS = frozenset(
    {"grep", "rg", "cat", "head", "tail", "less", "wc", "diff", "sed"},
)
INTERPRETERS = frozenset(
    {
        "python",
        "python3",
        "py",
        "uv",
        "perl",
        "node",
        "sh",
        "bash",
        "pwsh",
        "powershell",
    },
)
PATH_RUN = r"[\w./\\:~-]{1,260}"
PY_END = r"\.py(?![\w.])"
PY_TOKEN = re.compile(rf"{PATH_RUN}{PY_END}")
OPEN_FOR_WRITE = r"\bopen\((?=[^)]*(?:,\s*|mode\s*=\s*)['\"][rbt]*[wax+])"
CODE_WRITE = (
    rf"write_text|write_bytes|{OPEN_FOR_WRITE}|Set-Content|Out-File|Add-Content"
)
WRITE_LINE = re.compile(
    rf"(?:{CODE_WRITE})[^\n;&|]*{PY_END}|"
    rf"{PATH_RUN}{PY_END}['\"]?\)\.(?:write_text|write_bytes)\(|"
    rf"\b(?:sed|perl)\s+(?:-\S+\s+)*(?:-\w*i|--in-place)[^\n;&|]*{PY_END}",
)
REDIRECT_TO_PY = re.compile(
    rf"(?:>>?|\btee\s+(?:-\w+\s+)*)\s*[\"']?([^\s\"';|&<>]{{1,260}}{PY_END})",
)
SHELL_DENIED = (
    "{paths}: an existing .py in the repo is written "
    "from the shell. Change an existing .py with "
    "the Edit tool: the ruff hook formats it and the edit shows what changed."
)
USE_EDIT = (
    "{path} exists: change it with Edit, not Write. "
    "The ruff hook reformats a file after every "
    "write, so a second Write fails 'modified since "
    "read', and a whole-file rewrite hides what "
    "changed."
)
SHELL_REWRITE = (
    "{paths}: an existing .py was rewritten from the "
    "shell. Change an existing .py with Edit: "
    "the ruff hook formats it and the edit shows "
    "what changed. Check the rewrite now with "
    "git diff -- {paths}."
)


def py_targets(command: str, redirects_only: bool = False) -> set[str]:  # noqa: FBT001, FBT002
    spots = [] if redirects_only else [f[0] for f in WRITE_LINE.finditer(command)]
    spots += [found.group(1) for found in REDIRECT_TO_PY.finditer(command)]
    return {token for spot in spots for token in PY_TOKEN.findall(spot)}


def words_targets(words: list[str]) -> set[str]:
    program = program_name(words[0])
    text = " ".join(words)
    if program in SAFE_PROGRAMS:
        return set()
    reads = program in READ_PROGRAMS and not IN_PLACE_EDIT.search(text)
    return py_targets(text, redirects_only=reads)


def heredoc_targets(event: HookEvent) -> set[str]:
    targets: set[str] = set()
    for found in HEREDOC.finditer(event.command):
        start = event.command.rfind("\n", 0, found.start()) + 1
        lead = statements(
            event.command[start : found.end(1)],
            event.tool_name != "PowerShell",
        )
        programs = {s.program for s in lead}
        if not programs or programs & INTERPRETERS:
            targets |= py_targets(found.group(4))
    return targets


def command_targets(event: HookEvent) -> set[str]:
    found = heredoc_targets(event)
    for statement in shell_statements(event):
        for words in (statement.words, *statement.piped_to):
            found |= words_targets(words)
    return found


def existing_in_repo(token: str, cwd: str) -> str:
    path = Path(cwd or ROOT) / token
    try:
        return (
            path.resolve().relative_to(ROOT.resolve()).as_posix()
            if path.is_file()
            else ""
        )
    except ValueError:  # silent: a file outside the repo is not ours to guard
        return ""


def refused_before_shell(event: HookEvent) -> Verdict | None:
    if event.tool_name not in SHELLS:
        return None
    paths = sorted(
        {p for t in command_targets(event) if (p := existing_in_repo(t, event.cwd))},
    )
    return deny(NAME, SHELL_DENIED.format(paths=" ".join(paths))) if paths else None


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
    if event.tool_name in SHELLS:
        return refused_before_shell(event)
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
