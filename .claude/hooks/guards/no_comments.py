import importlib.util
from pathlib import Path
from types import ModuleType

from guards.event import ROOT, Guard, HookEvent, Verdict, block

NAME = "no_comments"
CHECKER = ROOT / "scripts" / "check_no_comments.py"
GETATTR_CHECKER = ROOT / "scripts" / "check_no_getattr.py"
FOUND = (
    "CLAUDE.md: NO COMMENTS, NO DOCSTRINGS, EVER: {spots}. Remove them; a descriptive name says it. "
    "Only machine directives stay (# noqa, # type: ignore, # fmt:, # pragma, # silent: <reason>)."
)


def checker(script: Path = CHECKER) -> ModuleType:
    spec = importlib.util.spec_from_file_location(script.stem, script)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(event: HookEvent) -> Verdict | None:
    if not event.relative:
        return None
    spots = checker().problems(ROOT, [event.relative])
    return block(NAME, FOUND.format(spots=", ".join(spots))) if spots else None


GUARD = Guard(
    name=NAME,
    events=frozenset({"PostToolUse"}),
    tools=frozenset({"Edit", "Write", "MultiEdit"}),
    check=check,
)
