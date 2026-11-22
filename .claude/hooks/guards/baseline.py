import json
import subprocess

from guards.event import (
    ROOT,
    Guard,
    HookEvent,
    Verdict,
    block,
    context,
    deny,
    git_timeout,
)

NAME = "baseline"
SHELLS = frozenset({"Bash", "PowerShell"})
BASELINE = "tests/unit/architecture/baseline.json"
ONLY_SHRINKS = (
    "CLAUDE.md: tests/unit/architecture/baseline.json lists old breaks and only shrinks. "
    "NEVER add a line to make a test pass: fix the code."
)


UNCOMPARED = (
    "The baseline could not be compared with HEAD (untracked, git failed or slow)."
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


def committed_baseline() -> str:
    shown = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"HEAD:{BASELINE}"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=git_timeout(),
        check=False,
    )
    return shown.stdout if shown.returncode == 0 else ""


def entries(text: str) -> set[str] | None:
    try:
        loaded = json.loads(text)
    except ValueError:  # silent: an unreadable baseline is reported as uncompared
        return None
    groups = loaded.items() if isinstance(loaded, dict) else [("", loaded)]
    return {
        f"{key}\0{json.dumps(item, sort_keys=True)}"
        for key, value in groups
        for item in (value if isinstance(value, list) else [value])
    }


def grown_by_shell(event: HookEvent) -> Verdict | None:
    path = ROOT / BASELINE
    if not path.is_file() or path.stat().st_mtime < event.started_at:
        return None
    try:
        committed = committed_baseline()
    except subprocess.SubprocessError:
        committed = ""
    now = entries(path.read_text(encoding="utf-8", errors="replace"))
    before = entries(committed) if committed else None
    if now is None or before is None:
        return context(NAME, f"{UNCOMPARED} {ONLY_SHRINKS}")
    if now - before:
        return block(NAME, f"{ONLY_SHRINKS} Revert it: git checkout -- {BASELINE}")
    return None


def check(event: HookEvent) -> Verdict | None:
    if event.hook_event_name == "PostToolUse":
        return grown_by_shell(event) if event.tool_name in SHELLS else None
    if event.relative != BASELINE:
        return None
    path = ROOT / BASELINE
    before = path.read_text(encoding="utf-8") if path.is_file() else ""
    grows = line_count(after_edit(event, before)) > line_count(before)
    return deny(NAME, ONLY_SHRINKS) if grows else None


GUARD = Guard(
    name=NAME,
    events=frozenset({"PreToolUse", "PostToolUse"}),
    tools=frozenset({"Edit", "Write", "MultiEdit", *SHELLS}),
    check=check,
)
