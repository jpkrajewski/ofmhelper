import re

from guards.event import Guard, HookEvent, Markers, Verdict

NAME = "owner_word"
SHIP_WORDS = re.compile(r"\b(ship|commit|push|deploy)\b", re.IGNORECASE)
PAID_WORD = re.compile(r"\bpaid\b", re.IGNORECASE)
GATE_WORD = re.compile(r"^\s*gate[.!]?\s*$", re.IGNORECASE)
OWNER_WORDS = (
    ("owner-ship", SHIP_WORDS),
    ("owner-paid", PAID_WORD),
    ("owner-gate", GATE_WORD),
)


def check(event: HookEvent) -> Verdict | None:
    if not event.is_owner_prompt:
        return None
    markers = Markers.of(event)
    for marker, word in OWNER_WORDS:
        if word.search(event.prompt):
            markers.set(marker)
        else:
            markers.clear(marker)
    return None


GUARD = Guard(name=NAME, events=frozenset({"UserPromptSubmit"}), check=check)
