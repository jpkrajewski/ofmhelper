import re
import subprocess
from pathlib import Path

from pydantic import BaseModel

from guards.event import (
    ROOT,
    Guard,
    HookEvent,
    Markers,
    Verdict,
    block,
    context,
    defined_names,
    deny,
    git_timeout,
    modified_at,
)
from guards.shell_writes import SCRIPTED_WRITE, changed_by_this_call
from guards.subagent_brief import READ_ONLY_AGENTS

NAME = "research_first"
NOTES = ROOT / "reports" / "research"
CODEGRAPH_TOOL = "mcp__codegraph__codegraph_explore"
WEB_TOOLS = frozenset({"WebSearch", "WebFetch"})
EDITS = frozenset({"Edit", "Write", "MultiEdit"})
SHELLS = frozenset({"Bash", "PowerShell"})
AGENTS = frozenset({"Agent", "Task"})
STOPS = frozenset({"Stop", "SubagentStop"})
CODE_ROOTS = ("src/", "scripts/", ".claude/hooks/")
CODE_SUFFIXES = (
    ".py",
    ".pyi",
    ".js",
    ".mjs",
    ".jsx",
    ".ts",
    ".tsx",
    ".sh",
    ".ps1",
    ".css",
    ".html",
    ".sql",
)
HEAD_HIT = re.compile(r"^HEAD:(.*?):\d+:(.*)$")
SKILL_DIRS = (".claude/skills/", ".claude/rules/")
CODEGRAPH_SHELL = re.compile(r"\bcodegraph\s+explore\b")
KIMI_RESEARCH = re.compile(r"kimi/ask\.py\s+research\b")
SKILL_GREP = re.compile(
    r"\b(?:grep|rg|findstr|select-string)\b[^\n]*\.claude[/\\](?:skills|rules)",
    re.IGNORECASE,
)
NOTE_PATH = re.compile(r"^reports/research/[^/]+\.md$")
URL = re.compile(r"https?://\S+")
NO_PATTERN = re.compile(r"^none\b", re.IGNORECASE)
LABELS = (
    ("Pattern in repo", "pattern"),
    ("Web", "web"),
    ("Skills", "skills"),
    ("Breaks when", "breaks"),
)
LABEL_LINE = re.compile(
    r"^[\s>*#-]*(pattern in repo|web|skills|breaks when)[\s*]*:[\s*]*(.*)$",
    re.IGNORECASE,
)
FAILURE_MODES = (
    (
        "service down or slow",
        re.compile(
            r"\bdown\b|slow|time ?outs?|unavailable|unreachable|offline", re.IGNORECASE
        ),
    ),
    (
        "backend 5xx, HTML or non-JSON",
        re.compile(r"5xx|\b50\d\b|non-json|\bhtml\b|backend error", re.IGNORECASE),
    ),
    (
        "crash after a side effect",
        re.compile(r"side[- ]effect|crash after|half[- ]done|replay", re.IGNORECASE),
    ),
    (
        "many clicks, two tabs, retries",
        re.compile(
            r"click|two tabs|double|concurren|\brac(?:e|es|y)\b|retr(?:y|ies)|duplicate|idempoten",
            re.IGNORECASE,
        ),
    ),
    (
        "junk input",
        re.compile(
            r"junk|malformed|invalid|garbage|bad input|unpars|emoji|validat",
            re.IGNORECASE,
        ),
    ),
    (
        "scale",
        re.compile(
            r"scale|\b\d+\s?k\b|large|huge|thousand|million|\bindex|retention",
            re.IGNORECASE,
        ),
    ),
    (
        "JS crash",
        re.compile(
            r"\bjs\b|javascript|pageerror|console error|browser|front[- ]?end",
            re.IGNORECASE,
        ),
    ),
)
TEMPLATE = (
    "Pattern in repo: <file and symbol the new code copies, or none>\n"
    "Web: <URLs read; required when the pattern is none>\n"
    "Skills: <skills and .claude/rules that apply>\n"
    "Breaks when: <what the code does when> the service is down or slow | the backend answers "
    "5xx, HTML or non-JSON | it crashes after a side effect | 50 clicks, two tabs, retries | "
    "junk input | scale | a JS crash"
)
HOW = (
    "1. codegraph_explore for the pattern in this repo (the shell's codegraph explore counts). "
    "2. If there is none: WebSearch how it is done at enterprise scale. "
    '3. Search the skills: git grep -n -i "<terms>" -- .claude/skills .claude/rules '
    "'*CLAUDE.md', or load the skill. "
    "4. Write reports/research/<slug>.md with these lines:\n" + TEMPLATE
)
STEPS = (
    "Research before new code (a new file under src/, scripts/ or .claude/hooks/, or a new "
    "def/class/function line): such an edit is denied until, since this task started: "
    + HOW
)
DENIED = (
    "Research before new code: {path} adds new code. Missing since this task started: "
    "{missing}. Moving code instead? Cut it from its old file first: a name the old file "
    "no longer defines is not new.\n" + HOW
)
SHELL_CODE = (
    "New code was written from the shell ({paths}) before the research: the Write/Edit gate "
    "never saw it. Missing since this task started: {missing}.\n"
    + HOW
    + "\nThen recheck those files against the research, and write new code with Write or Edit."
)


class ResearchNote(BaseModel):
    path: Path
    pattern: str = ""
    web: str = ""
    skills: str = ""
    breaks: str = ""

    @classmethod
    def read(cls, path: Path) -> ResearchNote:
        names = {label.lower(): name for label, name in LABELS}
        fields: dict[str, str] = {}
        current = ""
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if found := LABEL_LINE.match(line):
                current = names[found[1].lower()]
                fields[current] = found[2].strip()
            elif current and line.strip():
                fields[current] += " " + line.strip()
        return cls(path=path, **fields)

    def problems(self, searched_web: bool) -> list[str]:
        values = self.model_dump(exclude={"path"})
        found = [
            f"the line `{label}:`" for label, name in LABELS if not filled(values[name])
        ]
        if filled(self.breaks):
            found += [
                f"`Breaks when:` naming {mode}"
                for mode, words in FAILURE_MODES
                if not words.search(self.breaks)
            ]
        if NO_PATTERN.match(self.pattern):
            if not searched_web:
                found.append("a web search (the note says Pattern in repo: none)")
            if not URL.search(self.web):
                found.append("a URL in `Web:` (the note says Pattern in repo: none)")
        return found


def filled(value: str) -> bool:
    return bool(value) and not value.startswith("<")


def never_blocked(path: str) -> bool:
    return (
        not path
        or path.endswith((".md", "conftest.py"))
        or path.startswith(("reports/", "tests/"))
        or "/tests/" in path
    )


def is_new_code_file(path: str) -> bool:
    return (
        path.startswith(CODE_ROOTS)
        and path.lower().endswith(CODE_SUFFIXES)
        and not never_blocked(path)
    )


def still_defines(file: str, name: str) -> bool:
    target = ROOT / file
    if not target.is_file():
        return False
    return name in defined_names(target.read_text(encoding="utf-8", errors="replace"))


def moved_out_names(names: set[str], path: str) -> set[str]:
    if not names:
        return set()
    patterns = [
        rf"^[[:blank:]]*(async +def|def|class|export +(default +)?(async +)?function|"
        rf"(async +)?function)[ *]+{re.escape(name)}([^A-Za-z0-9_$]|$)"
        for name in sorted(names)
    ]
    try:
        found = subprocess.run(
            ["git", "-C", str(ROOT), "grep", "-n", "-E"]
            + [arg for pattern in patterns for arg in ("-e", pattern)]
            + ["HEAD", "--"]
            + [root.rstrip("/") for root in CODE_ROOTS],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=git_timeout(),
            check=False,
        )
    except subprocess.SubprocessError:  # silent: a slow git keeps the names new
        return set()
    moved: set[str] = set()
    for line in found.stdout.splitlines():
        spot = HEAD_HIT.match(line)
        if not spot or spot[1] == path:
            continue
        moved |= {
            name
            for name in defined_names(spot[2]) & names
            if not still_defines(spot[1], name)
        }
    return moved


def adds_new_code(event: HookEvent) -> bool:
    path = event.relative
    if never_blocked(path):
        return False
    names = event.added_names()
    fresh = names - moved_out_names(names, path)
    if is_new_code_file(path) and not (ROOT / path).exists():
        return bool(fresh) or not names
    return bool(fresh)


def names_skill_dir(text: str) -> bool:
    text = text.replace("\\", "/")
    return any(folder.rstrip("/") in text for folder in SKILL_DIRS)


def searched_skills(event: HookEvent) -> bool:
    name = event.tool_name
    if name == "Skill":
        return True
    if name == "Grep":
        return any(
            names_skill_dir(str(event.tool_input.get(key) or ""))
            for key in ("path", "glob", "pattern")
        )
    return name in SHELLS and bool(SKILL_GREP.search(event.command))


def searched_web(event: HookEvent) -> bool:
    name = event.tool_name
    if name in WEB_TOOLS:
        return True
    if name in AGENTS:
        return event.tool_input.get("subagent_type") == "web-fetch"
    return name in SHELLS and bool(KIMI_RESEARCH.search(event.command))


def looked_at_codegraph(event: HookEvent) -> bool:
    if event.tool_name == CODEGRAPH_TOOL:
        return True
    return event.tool_name in SHELLS and bool(CODEGRAPH_SHELL.search(event.command))


def shell_new_code(event: HookEvent) -> list[str]:
    if event.tool_name not in SHELLS or not SCRIPTED_WRITE.search(event.command):
        return []
    return [
        f.path
        for f in changed_by_this_call(event)
        if not never_blocked(f.path)
        and (
            (f.is_new and is_new_code_file(f.path))
            or bool((added := f.added_names()) - moved_out_names(added, f.path))
        )
    ]


def record(event: HookEvent) -> None:
    markers = Markers.of(event)
    if looked_at_codegraph(event):
        markers.set("looked-codegraph")
    if searched_web(event):
        markers.set("searched-web")
    if searched_skills(event):
        markers.set("searched-skills")
    if event.tool_name in EDITS and NOTE_PATH.match(event.relative):
        markers.set("research-note")
    if (paths := shell_new_code(event)) and missing_research(event):
        markers.set("shell-code", ", ".join(paths))


def fresh_notes(started: float) -> list[ResearchNote]:
    dated = [(modified_at(path), path) for path in NOTES.glob("*.md")]
    return [
        ResearchNote.read(path)
        for at, path in sorted(dated, reverse=True)
        if at > started
    ]


def note_problems(started: float, searched: bool) -> list[str]:
    notes = fresh_notes(started)
    if not notes:
        return ["a note reports/research/<slug>.md written since this task started"]
    found = [note.problems(searched) for note in notes]
    if any(not problems for problems in found):
        return []
    return [f"in {notes[0].path.name}: " + ", ".join(found[0])]


def missing_research(event: HookEvent) -> list[str]:
    markers = Markers.of(event)
    main = Markers.main(event)
    started = main.since("task-start")

    def done(name: str) -> bool:
        return max(markers.since(name), main.since(name)) > started

    missing = []
    if not done("looked-codegraph"):
        missing.append("codegraph_explore for the pattern")
    if not done("searched-skills"):
        missing.append("the skills search")
    return missing + note_problems(started, done("searched-web"))


def judge(event: HookEvent) -> Verdict | None:
    if event.tool_name not in EDITS or not adds_new_code(event):
        return None
    if missing := missing_research(event):
        return deny(
            NAME, DENIED.format(path=event.relative, missing="; ".join(missing))
        )
    return None


def stopped_after_shell_code(event: HookEvent) -> Verdict | None:
    markers = Markers.of(event)
    written = markers.since("shell-code")
    if event.stop_hook_active or written <= markers.since("shell-code-asked"):
        return None
    missing = missing_research(event)
    if not missing:
        return None
    markers.set("shell-code-asked")
    paths = markers.value("shell-code")
    return block(NAME, SHELL_CODE.format(paths=paths, missing="; ".join(missing)))


def started_by_owner(event: HookEvent) -> Verdict | None:
    if not event.is_owner_prompt:
        return None
    Markers.of(event).set("task-start")
    return context(NAME, STEPS)


def started_subagent(event: HookEvent) -> Verdict | None:
    main = Markers.main(event)
    if not main.has("task-start"):
        main.set("task-start")
    return None if event.agent_type in READ_ONLY_AGENTS else context(NAME, STEPS)


def check(event: HookEvent) -> Verdict | None:
    match event.hook_event_name:
        case "UserPromptSubmit":
            return started_by_owner(event)
        case "SubagentStart":
            return started_subagent(event)
        case "PostToolUse":
            record(event)
            return None
        case name if name in STOPS:
            return stopped_after_shell_code(event)
    return judge(event)


GUARD = Guard(
    name=NAME,
    events=frozenset(
        {"UserPromptSubmit", "SubagentStart", "PostToolUse", "PreToolUse", *STOPS}
    ),
    tools=frozenset(
        {*EDITS, *SHELLS, *AGENTS, *WEB_TOOLS, "Grep", "Skill", CODEGRAPH_TOOL}
    ),
    check=check,
)
