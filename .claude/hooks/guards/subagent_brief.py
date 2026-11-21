from guards.event import Guard, HookEvent, Verdict, context

NAME = "subagent_brief"
READ_ONLY_AGENTS = frozenset({"web-fetch", "Explore"})
BRIEF = (
    "Owner's contract for every subagent: (1) a question only the owner can answer (a destructive, "
    "paid or production choice, or one the brief leaves open): stop and return it in your report, "
    "never guess. (2) The main session runs the review and the worker restart after all agents "
    "finish: when a Stop hook asks you for either, say so in your report and stop. (3) A result is "
    "proven through the app's own page and queue on dev, not a scratch script. (4) Do only what the "
    "brief asks: no extra variants, files or features."
)


def check(event: HookEvent) -> Verdict | None:
    if event.agent_type in READ_ONLY_AGENTS:
        return None
    return context(NAME, BRIEF)


GUARD = Guard(name=NAME, events=frozenset({"SubagentStart"}), check=check)
