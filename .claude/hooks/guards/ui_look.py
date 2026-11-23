# Copyright (C) 2026 jpkrajewski

import re

from pydantic import BaseModel

from guards.event import ROOT, Guard, HookEvent, Markers, Verdict, block, context
from guards.shell import Statement, program_name, shell_statements
from guards.shell_writes import SCRIPTED_WRITE, changed_by_this_call

NAME = "look_at_the_ui"
EDITS = {"Edit", "Write", "MultiEdit"}
SHELLS = {"Bash", "PowerShell"}
UI_FILES = re.compile(r"^src/ofmhelpers/web/(templates|static)/")
UI_CODE = re.compile(r"^src/ofmhelpers/web/(templates/|static/.*\.js$)")
UI_SCREENS = "ui_screens"
UI_CHECK = "ui_check.py"
UI_CHECK_REPORTS = "reports/ui-check/"
UI_CHECK_REPORT = "report.md"
INTERPRETERS = frozenset({"python", "python3", "py"})
LAUNCHERS = INTERPRETERS | {"uv", "docker"}
SCRIPT_RUNNERS = INTERPRETERS | {"run"}
UI_CHECK_VERDICT = re.compile(
    r"^[ \t]*ui_check:[ \t]*(PASS|FAIL|DOWN)\b(.*)$",
    re.MULTILINE,
)
UI_CHECK_REPORT_PASSED = re.compile(r"^- Result: PASS\b", re.MULTILINE)
LOOK_FIRST = (
    "CLAUDE.md ui.md: every UI change is looked "
    "at before it is done. Fast, one page: run "
    "scripts/ui_screens.py's sign_in + shoot for the "
    "page you touched (both widths) in the tests "
    "container, then Read the png and compare it with "
    "the cards next to it (same height, same grid, "
    "nothing overflowing). All pages: scripts/ui_screens.py "
    "rNN. Then the owner checks dev."
)
CHECK_FIRST = (
    "CLAUDE.md ui.md: a JS or template change is done "
    "only after the edge-case UI check: load the "
    "ui-check skill and run scripts/ui_check.py <page> "
    "in the tests container against dev (a 500, "
    "an HTML answer, offline, a slow answer, 50 clicks, "
    "junk input), read its report.md and PNGs, "
    "fix what fails and rerun until it passes. Then the owner checks dev."
)
NOTE = (
    "UI file changed: before calling it done, load the "
    "ui-check skill and run scripts/ui_check.py "
    "on the page against dev; a JS or template change "
    "counts as looked at only after it passes. "
    "A CSS-only change: scripts/ui_screens.py shoot at both widths is enough."
)


class ShellOutput(BaseModel):
    stdout: str = ""

    @classmethod
    def of(cls, event: HookEvent) -> ShellOutput:
        raw = (event.model_extra or {}).get("tool_response")
        if isinstance(raw, dict):
            return cls.model_validate(raw)
        return cls(stdout=raw if isinstance(raw, str) else "")

    def ui_check_passed(self, started_at: float) -> bool:
        verdicts = UI_CHECK_VERDICT.findall(self.stdout)
        if not verdicts or verdicts[-1][0] != "PASS":
            return False
        return report_passed(verdicts[-1][1].strip(), started_at)


def report_passed(reported: str, started_at: float) -> bool:
    _, found, rest = reported.replace("\\", "/").partition(UI_CHECK_REPORTS)
    if not found:
        return False
    report = (ROOT / UI_CHECK_REPORTS / rest).resolve()
    return (
        report.name == UI_CHECK_REPORT
        and report.is_relative_to((ROOT / UI_CHECK_REPORTS).resolve())
        and report.is_file()
        and report.stat().st_mtime >= started_at
        and bool(
            UI_CHECK_REPORT_PASSED.search(
                report.read_text(encoding="utf-8", errors="replace"),
            ),
        )
    )


def runs_file(statement: Statement, filename: str) -> bool:
    words = statement.words
    if statement.program == filename:
        return True
    return launched(statement) and any(
        program_name(word) == filename
        and program_name(words[index - 1]) in SCRIPT_RUNNERS
        for index, word in enumerate(words[1:], start=1)
    )


def ran_ui_check(event: HookEvent) -> bool:
    return event.tool_name in SHELLS and any(
        runs_file(s, UI_CHECK) for s in shell_statements(event)
    )


def launched(statement: Statement) -> bool:
    return statement.program in LAUNCHERS or statement.program.startswith("$")


def runs_script(statement: Statement, script: str) -> bool:
    if script in statement.program:
        return True
    words = statement.words
    return launched(statement) and any(
        script in word
        and (
            program_name(words[index - 1]) in SCRIPT_RUNNERS
            or (
                words[index - 1] == "-c"
                and index > 1
                and program_name(words[index - 2]) in INTERPRETERS
            )
        )
        for index, word in enumerate(words[1:], start=1)
    )


def ran(event: HookEvent, script: str) -> bool:
    return event.tool_name in SHELLS and any(
        runs_script(s, script) for s in shell_statements(event)
    )


def changed_ui(event: HookEvent) -> list[str]:
    if event.tool_name in EDITS:
        paths = [event.relative]
    elif event.tool_name in SHELLS and SCRIPTED_WRITE.search(event.command):
        paths = [f.path for f in changed_by_this_call(event)]
    else:
        return []
    return [path for path in paths if UI_FILES.search(path)]


def looked_at(markers: Markers) -> float:
    return max(markers.since("ui-looked"), markers.since("ui-checked"))


def record(event: HookEvent, markers: Markers) -> Verdict | None:
    if ran_ui_check(event):
        if ShellOutput.of(event).ui_check_passed(event.started_at):
            markers.set("ui-checked")
        return None
    if ran(event, UI_SCREENS):
        markers.set("ui-looked")
        return None
    changed = changed_ui(event)
    if not changed:
        return None
    note_due = markers.since("ui-noted") <= looked_at(markers)
    markers.set("ui-changed")
    if any(UI_CODE.search(path) for path in changed):
        markers.set("ui-code-changed")
    if not note_due:
        return None
    markers.set("ui-noted")
    return context(NAME, NOTE)


def check(event: HookEvent) -> Verdict | None:
    markers = Markers.of(event)
    if event.hook_event_name == "PostToolUse":
        return record(event, markers)
    if event.stop_hook_active:
        return None
    asked = markers.since("look-asked")
    if markers.since("ui-code-changed") > max(markers.since("ui-checked"), asked):
        markers.set("look-asked")
        return block(NAME, CHECK_FIRST)
    if markers.since("ui-changed") > max(looked_at(markers), asked):
        markers.set("look-asked")
        return block(NAME, LOOK_FIRST)
    return None


GUARD = Guard(
    name=NAME,
    events=frozenset({"PostToolUse", "Stop", "SubagentStop"}),
    tools=frozenset({*EDITS, *SHELLS}),
    check=check,
)
