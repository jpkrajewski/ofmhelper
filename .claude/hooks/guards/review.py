import re

from guards.event import Guard, HookEvent, Markers, Verdict, block

NAME = "review_before_done"
EDITS = {"Edit", "Write", "MultiEdit"}
AGENTS = {"Agent", "Task"}
SHELLS = {"Bash", "PowerShell"}
REVIEW_WORDS = re.compile(r"review", re.IGNORECASE)
KIMI_RUN = re.compile(
    r"kimi/ask\.py\s+review|kimi\.exe|(^|[;&|(]\s*)kimi\s+(-p|-r)\b", re.MULTILINE
)
KIMI_FIRST = (
    "MY-RULES: before calling a feature done, an independent reviewer checks it for failures, races, "
    "scale and safety, and you fix what it finds. The reviewer is Kimi: the kimi skill, "
    "python .claude/skills/kimi/ask.py review <task.md> <name> on the diff. Only if Kimi fails or "
    "times out, a cavecrew-reviewer agent as backup (say why Kimi failed). "
    "If this stop is a question to the owner, not done, say so and stop again."
)


def is_kimi(event: HookEvent) -> bool:
    return event.tool_name in SHELLS and bool(KIMI_RUN.search(event.command.lower()))


def is_review_agent(event: HookEvent) -> bool:
    text = f"{event.tool_input.get('description', '')} {event.tool_input.get('subagent_type', '')}"
    return event.tool_name in AGENTS and bool(REVIEW_WORDS.search(text))


def record(event: HookEvent, markers: Markers) -> None:
    if event.tool_name in EDITS and event.relative.startswith("src/"):
        markers.set("edited-src")
    elif is_kimi(event):
        markers.set("kimi-review")
    elif is_review_agent(event):
        markers.set("agent-review")


def reviewed_since(markers: Markers, edited: float) -> bool:
    kimi = markers.since("kimi-review")
    backup = markers.since("agent-review")
    return kimi > edited or (backup > edited and kimi > 0)


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
    return block(NAME, KIMI_FIRST)


GUARD = Guard(
    name=NAME,
    events=frozenset({"PostToolUse", "Stop", "SubagentStop"}),
    tools=frozenset({*EDITS, *AGENTS, *SHELLS}),
    check=check,
)
