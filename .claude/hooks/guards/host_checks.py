# Copyright (C) 2026 jpkrajewski

from guards.event import Guard, HookEvent, Verdict, deny
from guards.shell import Statement, shell_statements

NAME = "host_checks"
CHECK_TOOLS = {"mypy", "biome", "djlint"}
DOCKER = (
    "CLAUDE.md: every check runs in Docker dev, never on the host: "
    "docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm tests "
    "uv run --frozen {tool} ...  (tests: scripts/test.sh; "
    "templates: djlint; JS: biome lint)."
)


def checked_tool(statement: Statement) -> str:
    program = statement.program
    if program in CHECK_TOOLS:
        return program
    if program == "ruff" and statement.words[1:2] in (["check"], ["format"]):
        return f"ruff {statement.words[1]}"
    if program == "node" and "--check" in statement.words:
        return "node --check"
    return ""


def check(event: HookEvent) -> Verdict | None:
    for statement in shell_statements(event):
        if statement.program in ("docker", "ssh", "pre-commit"):
            continue
        if tool := checked_tool(statement):
            return deny(NAME, DOCKER.format(tool=tool))
    return None


GUARD = Guard(
    name=NAME,
    events=frozenset({"PreToolUse"}),
    tools=frozenset({"Bash", "PowerShell"}),
    check=check,
)
