import re

from guards.event import Guard, HookEvent, Verdict, block

NAME = "finish_the_work"
TAIL_CHARS = 800
HANDS_BACK = re.compile(
    r"\b(tell me (which|if|whether)|which (route|option|one|way) (do )?you|do you want|would you like|"
    r"should i\b|want me to|let me know|your call|say if|held off|did not build|didn't build|"
    r"before i (build|start|go on))",
    re.IGNORECASE,
)
DECIDE = (
    "CLAUDE.md THE RULES DECIDE + JUST FIX IT + DONE MEANS DONE: a question a rule or the task answers "
    "is not for the owner. Re-read the task (it names the fix), CLAUDE.md and the STANDARDS skills, pick "
    "the best default, build it, run the scoped tests, then report. The owner decides: a destructive, "
    "paid or production choice, and anything the owner's task says to decide together, brainstorm or "
    "ask them about. If this is one of those, quote the line of the task or the rule that makes it "
    "one, ask the question, and stop again: this stop then goes through."
)


def check(event: HookEvent) -> Verdict | None:
    if event.stop_hook_active:
        return None
    tail = event.last_assistant_message[-TAIL_CHARS:]
    return block(NAME, DECIDE) if HANDS_BACK.search(tail) else None


GUARD = Guard(name=NAME, events=frozenset({"Stop", "SubagentStop"}), check=check)
