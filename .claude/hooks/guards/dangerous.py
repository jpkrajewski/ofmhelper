from guards.event import Guard, HookEvent, Verdict, deny
from guards.shell import UNREADABLE, Statement, shell_statements

NAME = "dangerous"
UNREADABLE_COMMAND = (
    "The guards cannot read this command (an undecodable powershell -EncodedCommand, or scripts "
    "nested more than 3 deep), so none of their rules could be checked. Write the command out plainly."
)
DRIVE_ROOTS = {"", "/c", "c:", "C:"}
RUF100 = (
    "ruff --select RUF100 --fix deletes every # noqa that is not for a selected rule "
    "(101 removed on 2026-11-08). Clear stale noqas with a plain ruff check --fix."
)
FIND_ROOT = (
    "Never search the whole disk from this Windows host: an orphaned find keeps crawling for hours "
    "after the shell times out. Search a bounded path, or look a library up inside the tests image."
)
BARE_COMPOSE = (
    "Locally every docker compose command takes both files: "
    "docker compose -f docker-compose.yml -f docker-compose.dev.yml ... "
    "(docker-compose.yml alone is production)."
)
DOWN_VOLUMES = (
    "down -v deletes the data volumes. Never; stop or recreate containers instead."
)
SSH_STDIN = (
    "Never pipe a script into ssh ... bash -s: the first command that reads stdin eats the rest "
    "and bash exits 0 (2026-07-27 deploy). Copy the script to the server and run that file."
)
SCP_ENV = (
    "Never copy a .env onto a server: the server .env holds production secrets. "
    "Change it key by key, with a backup first."
)


def problem(statement: Statement) -> str:
    words, program = statement.words, statement.program
    if program == UNREADABLE:
        return UNREADABLE_COMMAND
    if (
        program == "ruff"
        and "--fix" in words
        and any("RUF100" in word for word in words)
    ):
        return RUF100
    if program == "find" and len(words) > 1 and words[1].rstrip("/\\") in DRIVE_ROOTS:
        return FIND_ROOT
    if program == "docker" and words[1:2] == ["compose"]:
        if "down" in words and ("-v" in words or "--volumes" in words):
            return DOWN_VOLUMES
        if "-f" not in words and "--file" not in words:
            return BARE_COMPOSE
    if program == "ssh" and (
        ("bash" in words and "-s" in words) or any("bash -s" in w for w in words)
    ):
        return SSH_STDIN
    if (
        program == "scp"
        and ":" in words[-1]
        and any(w.endswith(".env") for w in words[1:-1])
    ):
        return SCP_ENV
    return ""


def check(event: HookEvent) -> Verdict | None:
    return next(
        (deny(NAME, reason) for s in shell_statements(event) if (reason := problem(s))),
        None,
    )


GUARD = Guard(
    name=NAME,
    events=frozenset({"PreToolUse"}),
    tools=frozenset({"Bash", "PowerShell"}),
    check=check,
)
