import re

from guards.event import Guard, HookEvent, Markers, Verdict, block, context
from guards.shell_writes import SCRIPTED_WRITE, changed_by_this_call

NAME = "restart_workers"
AFTER_A_CALL = {"PostToolUse", "PostToolUseFailure"}
EDITS = {"Edit", "Write", "MultiEdit"}
SHELLS = {"Bash", "PowerShell"}
WORKER_CODE = re.compile(r"^src/ofmhelpers/(?!web/).*\.(py|json)$")
RESTART = re.compile(r"\b(restart|up\s+-d)\b[^;&|]*\b(worker|media-worker|sweeps)\b")
RESTART_FIRST = (
    "CLAUDE.local: dev workers and sweeps have no reloader, so they still run the old code you changed. "
    "Restart them before calling it done: docker compose -f docker-compose.yml -f docker-compose.dev.yml "
    "restart worker media-worker preview-worker edit-worker sweeps, then read their logs for a traceback."
)


def changed_worker_code(event: HookEvent) -> bool:
    if event.tool_name in EDITS:
        return bool(WORKER_CODE.search(event.relative))
    if event.tool_name not in SHELLS or not SCRIPTED_WRITE.search(event.command):
        return False
    return any(WORKER_CODE.search(f.path) for f in changed_by_this_call(event))


def check(event: HookEvent) -> Verdict | None:
    markers = Markers.of(event)
    if event.hook_event_name in AFTER_A_CALL:
        if event.tool_name in SHELLS and RESTART.search(event.command):
            markers.set("workers-restarted")
        elif changed_worker_code(event):
            note_due = markers.since("restart-noted") <= markers.since(
                "workers-restarted"
            )
            markers.set("worker-code")
            if note_due:
                markers.set("restart-noted")
                return context(NAME, RESTART_FIRST)
        return None
    changed = markers.since("worker-code")
    if event.stop_hook_active or not changed:
        return None
    if (
        markers.since("workers-restarted") > changed
        or markers.since("restart-asked") > changed
    ):
        return None
    markers.set("restart-asked")
    return block(NAME, RESTART_FIRST)


GUARD = Guard(
    name=NAME,
    events=frozenset({*AFTER_A_CALL, "Stop", "SubagentStop"}),
    tools=frozenset({*EDITS, *SHELLS}),
    check=check,
)
