import re
import subprocess
from pathlib import Path

from pydantic import BaseModel

from guards.event import (
    ROOT,
    Guard,
    HookEvent,
    Verdict,
    block,
    context,
    defined_names,
    git_timeout,
)
from guards.no_comments import GETATTR_CHECKER, checker
from guards.skills import loaded, required

NAME = "shell_writes"
RUFF_WRITE = (
    r"\bruff(?:\.exe)?\s+(?![^;&|\n#]*--(?:check|diff)\b)"
    r"(?:(?:-\S+\s+)*format\b|[^;&|\n#]*--fix\b)"
)
SCRIPTED_WRITE = re.compile(
    r"\bsed\s+-i|\bpython|\bperl\b|\bawk\b|\bcat\s+>|\btee\b|[^0-9&]>\s*[\w./\"']|"
    r"Set-Content|Out-File|Add-Content|\bcp\s|\bmv\s|\bgit\s+apply\b|\bpatch\b|"
    rf"{RUFF_WRITE}|\bprettier\b[^;&|]*--write"
)
UNNAMED_WRITE = re.compile(rf"\bgit\s+apply\b|\bpatch\b|{RUFF_WRITE}|\bprettier\b")
UNCHECKED = (
    "The files this shell call wrote could not be checked ({error}); git was slow or failed. "
    "Run the CRLF, comment and skill checks by hand on the files you wrote."
)
CRLF = "CRLF written into {files} (Windows text mode; .gitattributes is eol=lf): rewrite them with LF."
COMMENTS = "Comments or docstrings written from the shell: {spots} (CLAUDE.md NO COMMENTS, EVER)."
GETATTR = "getattr written from the shell (banned, owner 2026-11-22): {spots}"
SKILLS = (
    "Edited from the shell without the STANDARDS skill(s) {skills}: load them now and re-check "
    "{files} against them. Prefer the Edit tool for source files."
)


class ChangedFile(BaseModel):
    path: str
    is_new: bool

    def added_names(self) -> set[str]:
        now = (ROOT / self.path).read_text(encoding="utf-8", errors="replace")
        before = "" if self.is_new else committed_text(self.path)
        return defined_names(now) - defined_names(before)

    def skills(self) -> list[str]:
        text = (ROOT / self.path).read_text(encoding="utf-8", errors="replace")
        plain = required(self.path, self.is_new, adds_names=False, written=text)
        if plain == required(self.path, self.is_new, adds_names=True, written=text):
            return plain
        return required(
            self.path, self.is_new, adds_names=bool(self.added_names()), written=text
        )


def committed_text(path: str) -> str:
    shown = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"HEAD:{path}"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=git_timeout(),
        check=False,
    )
    return shown.stdout if shown.returncode == 0 else ""


def changed_by_this_call(event: HookEvent) -> list[ChangedFile]:
    listed = subprocess.run(
        ["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        timeout=git_timeout(),
        check=True,
    )
    cutoff = event.started_at
    found = [
        ChangedFile(
            path=line[3:].split(" -> ")[-1].strip('"'), is_new=line.startswith("??")
        )
        for line in listed.stdout.splitlines()
    ]
    return [
        f
        for f in found
        if named_by(event.command, f.path)
        and (ROOT / f.path).is_file()
        and (ROOT / f.path).stat().st_mtime >= cutoff
    ]


def named_by(command: str, path: str) -> bool:
    return (
        bool(UNNAMED_WRITE.search(command))
        or path in command
        or Path(path).name in command
    )


def problems(event: HookEvent, files: list[ChangedFile]) -> list[str]:
    paths = [f.path for f in files]
    crlf = [p for p in paths if b"\r\n" in (ROOT / p).read_bytes()]
    spots = checker().problems(ROOT, paths)
    banned = checker(GETATTR_CHECKER).problems(ROOT, paths)
    missing = sorted({s for f in files for s in f.skills() if not loaded(event, s)})
    found = []
    if crlf:
        found.append(CRLF.format(files=", ".join(crlf)))
    if spots:
        found.append(COMMENTS.format(spots=", ".join(spots)))
    if banned:
        found.append(GETATTR.format(spots="; ".join(banned)))
    if missing:
        found.append(SKILLS.format(skills=", ".join(missing), files=", ".join(paths)))
    return found


def check(event: HookEvent) -> Verdict | None:
    if not SCRIPTED_WRITE.search(event.command):
        return None
    try:
        files = changed_by_this_call(event)
        found = problems(event, files) if files else []
    except (OSError, subprocess.SubprocessError) as error:
        return context(NAME, UNCHECKED.format(error=type(error).__name__))
    return block(NAME, "\n".join(found)) if found else None


GUARD = Guard(
    name=NAME,
    events=frozenset({"PostToolUse"}),
    tools=frozenset({"Bash", "PowerShell"}),
    check=check,
)
