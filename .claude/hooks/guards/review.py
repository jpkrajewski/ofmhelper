import re

from guards.event import Guard, HookEvent, Markers, Verdict, block
from guards.shell_writes import SCRIPTED_WRITE, changed_by_this_call

NAME = "review_before_done"
EDITS = {"Edit", "Write", "MultiEdit"}
AGENTS = {"Agent", "Task"}
SHELLS = {"Bash", "PowerShell"}
REVIEW_WORDS = re.compile(r"review", re.IGNORECASE)
KIMI_RUN = re.compile(
    r"kimi/ask\.py\s+review|kimi\.exe|(^|[;&|(]\s*)kimi\s+(-p|-r)\b", re.MULTILINE
)
REVIEWER_FIRST = (
    "Owner (2026-11-20): before calling a feature done, an independent reviewer checks it for failures, "
    "races, scale and safety, and you fix what it finds. The reviewer is the cavecrew reviewer: the Agent "
    "tool with subagent_type caveman:cavecrew-reviewer on the diff. Only if it fails or times out, Kimi as "
    "backup: python .claude/skills/kimi/ask.py review <task.md> <name> (say why the reviewer failed). "
    "If this stop is a question to the owner, not done, say so and stop again."
)


def is_kimi(event: HookEvent) -> bool:
    return event.tool_name in SHELLS and bool(KIMI_RUN.search(event.command.lower()))


def is_review_agent(event: HookEvent) -> bool:
    text = f"{event.tool_input.get('description', '')} {event.tool_input.get('subagent_type', '')}"
    return event.tool_name in AGENTS and bool(REVIEW_WORDS.search(text))


def is_code(path: str) -> bool:
    return path.startswith("src/") and not path.endswith(".md")


def edited_source(event: HookEvent) -> bool:
    if event.tool_name in EDITS:
        return is_code(event.relative)
    if event.tool_name not in SHELLS or not SCRIPTED_WRITE.search(event.command):
        return False
    return any(is_code(f.path) for f in changed_by_this_call(event))


def record(event: HookEvent, markers: Markers) -> None:
    if edited_source(event):
        markers.set("edited-src")
    elif is_kimi(event):
        markers.set("kimi-review")
    elif is_review_agent(event):
        markers.set("agent-review")


def reviewed_since(markers: Markers, edited: float) -> bool:
    reviewer = markers.since("agent-review")
    backup = markers.since("kimi-review")
    return reviewer > edited or (backup > edited and reviewer > 0)


def check(event: HookEvent) -> Verdict | None:
    markers = Markers.of(event)
    if event.hook_event_name == "PostToolUse":
        record(event, markers)
        return None
    edited = markers.since("edited-src")
    if event.stop_hook_active or not edited:
        return None
    if reviewed_since(markers, edited) or markers.since("review-asked") > edited:
        return None
    markers.set("review-asked")
    return block(NAME, REVIEWER_FIRST)


GUARD = Guard(
    name=NAME,
    events=frozenset({"PostToolUse", "Stop", "SubagentStop"}),
    tools=frozenset({*EDITS, *AGENTS, *SHELLS}),
    check=check,
)
