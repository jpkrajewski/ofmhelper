from guards.event import ROOT, Guard, HookEvent, Verdict, deny

NAME = "baseline"
BASELINE = "tests/unit/architecture/baseline.json"
ONLY_SHRINKS = (
    "CLAUDE.md: tests/unit/architecture/baseline.json lists old breaks and only shrinks. "
    "NEVER add a line to make a test pass: fix the code."
)


def line_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def after_edit(event: HookEvent, before: str) -> str:
    if event.tool_name == "Write":
        return str(event.tool_input.get("content") or "")
    text = before
    for edit in event.tool_input.get("edits") or [event.tool_input]:
        old = str(edit.get("old_string") or "")
        new = str(edit.get("new_string") or "")
        text = (
            text.replace(old, new)
            if edit.get("replace_all")
            else text.replace(old, new, 1)
        )
    return text


def check(event: HookEvent) -> Verdict | None:
    if event.relative != BASELINE:
        return None
    path = ROOT / BASELINE
    before = path.read_text(encoding="utf-8") if path.is_file() else ""
    grows = line_count(after_edit(event, before)) > line_count(before)
    return deny(NAME, ONLY_SHRINKS) if grows else None


GUARD = Guard(
    name=NAME,
    events=frozenset({"PreToolUse"}),
    tools=frozenset({"Edit", "Write", "MultiEdit"}),
    check=check,
)
