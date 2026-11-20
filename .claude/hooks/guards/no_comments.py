import importlib.util
from types import ModuleType

from guards.event import ROOT, Guard, HookEvent, Verdict, block

NAME = "no_comments"
CHECKER = ROOT / "scripts" / "check_no_comments.py"
FOUND = (
    "CLAUDE.md: NO COMMENTS, NO DOCSTRINGS, EVER: {spots}. Remove them; a descriptive name says it. "
    "Only machine directives stay (# noqa, # type: ignore, # fmt:, # pragma, # silent: <reason>)."
)


def checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_no_comments", CHECKER)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(CHECKER)
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
