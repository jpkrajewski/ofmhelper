import re

from guards.event import ROOT, Guard, HookEvent, Verdict, context

NAME = "reminders"
CLAUDE_MD = ROOT / "CLAUDE.md"
SECTIONS = ("## HOW TO WORK", "## CODE RULES AGENTS KEEP BREAKING")
LEAD = re.compile(r"^- \*\*(.+?)\*\*", re.MULTILINE)


def section(text: str, heading: str) -> str:
    start = text.find(heading)
    if start < 0:
        return ""
    end = text.find("\n## ", start + len(heading))
    return text[start : end if end > 0 else None]


def check(event: HookEvent) -> Verdict | None:
    if not event.is_owner_prompt:
        return None
    text = CLAUDE_MD.read_text(encoding="utf-8")
    leads = [
        lead.rstrip(".")
        for heading in SECTIONS
        for lead in LEAD.findall(section(text, heading))
    ]
    return (
        context(NAME, "CLAUDE.md, in force all session: " + " | ".join(leads))
        if leads
        else None
    )


GUARD = Guard(name=NAME, events=frozenset({"UserPromptSubmit"}), check=check)
