# Copyright (C) 2026 jpkrajewski

import io
import re
import textwrap
import tokenize

from guards.event import Guard, HookEvent, Verdict, deny

NAME = "no_getattr"
BANNED = frozenset(
    {"getattr", "attrgetter", "__getattribute__", "getattr_static", "vars", "__dict__"},
)
REFUSED = (
    "getattr, vars() and __dict__ are banned in the repo (owner 2026-11-22: you and "
    "other agents cannot use them); this edit writes {count} use(s) of getattr / "
    "attrgetter / __getattribute__ / getattr_static / vars / __dict__ into {path}. "
    "Read the typed field, validate the object with from_attributes=True, iterate the "
    "model, or key a read-only mapping by the name (pydantic skill). Checked again by "
    "scripts/check_no_getattr.py (pre-commit) and "
    "tests/unit/architecture/test_no_getattr.py."
)


def code_uses(fragment: str, banned: frozenset[str]) -> int:
    text, found = textwrap.dedent(fragment), 0
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            found += token.type == tokenize.NAME and token.string in banned
    except tokenize.TokenError:
        return found
    except SyntaxError as error:
        unread = text.splitlines()[max((error.lineno or 1) - 1, 0) :]
        words = re.findall(r"[A-Za-z_]\w*", "\n".join(unread))
        return found + sum(word in banned for word in words)
    return found


def check(event: HookEvent) -> Verdict | None:
    if not event.relative.endswith(".py"):
        return None
    count = code_uses(event.written_text, BANNED)
    if not count:
        return None
    return deny(NAME, REFUSED.format(count=count, path=event.relative))


GUARD = Guard(
    name=NAME,
    events=frozenset({"PreToolUse"}),
    tools=frozenset({"Edit", "Write", "MultiEdit"}),
    check=check,
)
