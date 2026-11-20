import re

from guards.event import Guard, HookEvent, Markers, Verdict, block
from guards.shell_writes import SCRIPTED_WRITE, changed_by_this_call

NAME = "look_at_the_ui"
EDITS = {"Edit", "Write", "MultiEdit"}
SHELLS = {"Bash", "PowerShell"}
UI_FILES = re.compile(r"^src/ofmhelpers/web/(templates|static)/")
SCREENSHOT = re.compile(r"ui_screens|\.shoot\(|screenshot\(")
LOOK_FIRST = (
    "CLAUDE.md ui.md: every UI change is looked at before it is done. Fast, one page: run "
    "scripts/ui_screens.py's sign_in + shoot for the page you touched (both widths) in the tests "
    "container, then Read the png and compare it with the cards next to it (same height, same grid, "
    "nothing overflowing). All pages: scripts/ui_screens.py rNN. Then the owner checks dev."
)


def changed_ui(event: HookEvent) -> bool:
    if event.tool_name in EDITS:
        return bool(UI_FILES.search(event.relative))
    if event.tool_name not in SHELLS or not SCRIPTED_WRITE.search(event.command):
        return False
    return any(UI_FILES.search(f.path) for f in changed_by_this_call(event))


def check(event: HookEvent) -> Verdict | None:
    markers = Markers.of(event)
    if event.hook_event_name == "PostToolUse":
        if event.tool_name in SHELLS and SCREENSHOT.search(event.command):
            markers.set("ui-looked")
        elif changed_ui(event):
            markers.set("ui-changed")
        return None
    changed = markers.since("ui-changed")
    if event.stop_hook_active or not changed:
        return None
    if markers.since("ui-looked") > changed or markers.since("look-asked") > changed:
        return None
    markers.set("look-asked")
    return block(NAME, LOOK_FIRST)


GUARD = Guard(
    name=NAME,
    events=frozenset({"PostToolUse", "Stop", "SubagentStop"}),
    tools=frozenset({*EDITS, *SHELLS}),
    check=check,
)
