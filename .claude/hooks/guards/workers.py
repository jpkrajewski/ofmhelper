import re
import subprocess

from guards.docker_up import docker_cli
from guards.event import (
    Guard,
    HookEvent,
    Markers,
    Verdict,
    block,
    budget_timeout,
    context,
)
from guards.shell import Statement, shell_statements
from guards.shell_writes import SCRIPTED_WRITE, changed_by_this_call

NAME = "restart_workers"
AFTER_A_CALL = {"PostToolUse", "PostToolUseFailure"}
EDITS = {"Edit", "Write", "MultiEdit"}
SHELLS = {"Bash", "PowerShell"}
WORKER_CODE = re.compile(r"^src/ofmhelpers/(?!web/).*\.(py|json)$")
COMPOSE_VERBS = {"restart", "up"}
COMPOSE_VALUE_FLAGS = frozenset(
    {
        "-f",
        "--file",
        "-p",
        "--project-name",
        "--profile",
        "--project-directory",
        "--env-file",
        "--ansi",
        "--parallel",
        "-t",
        "--timeout",
        "--scale",
        "--pull",
        "--wait-timeout",
    }
)
UNCHECKED = (
    "The files this shell call wrote could not be checked for worker code (git was slow or failed). "
    "If you changed code under src/ofmhelpers (not web/), restart the dev workers and sweeps."
)
REDIS_CONTAINER = "ofmhelpers-redis"
REDIS_TIMEOUT_S = 5
SERVICE_QUEUES = (
    ("worker", ("default", "clone-links", "events")),
    ("media-worker", ("media",)),
    ("preview-worker", ("previews",)),
    ("edit-worker", ("edit",)),
    ("sweeps", ()),
)
SERVICE_NAMES = frozenset(service for service, _ in SERVICE_QUEUES)
RESTARTED = "restarted-"
QUEUES = tuple(queue for _, queues in SERVICE_QUEUES for queue in queues)
RESTART_FIRST = (
    "CLAUDE.local: dev workers and sweeps have no reloader, so they still run the old code you changed. "
    "Restart them before calling it done: docker compose -f docker-compose.yml -f docker-compose.dev.yml "
    "restart {services}, then read their logs for a traceback. Right before restarting, re-check that "
    "no job runs (docker exec ofmhelpers-redis redis-cli zcard rq:wip:<queue> is 0): the answer above "
    "is from the moment the code changed."
)
NOT_NOW = (
    " Do NOT restart {services}: a job is running there, or the queue could not be read, and a "
    "restart cuts the job (dev recovery leaves its row running forever). Say in your report that "
    "they still run the old code; the main session restarts them once no job runs."
)


class UncheckedWriteError(Exception):
    pass


def busy_queues() -> set[str] | None:
    try:
        done = subprocess.run(
            [*docker_cli(), "exec", "-i", REDIS_CONTAINER, "redis-cli"],
            input="".join(f"zcard rq:wip:{queue}\n" for queue in QUEUES),
            capture_output=True,
            text=True,
            timeout=budget_timeout(REDIS_TIMEOUT_S, REDIS_CONTAINER),
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:  # silent: unknown = not safe to restart
        return None
    counts = done.stdout.split()
    if done.returncode != 0 or len(counts) != len(QUEUES):
        return None
    if not all(count.isdigit() for count in counts):
        return None
    return {queue for queue, count in zip(QUEUES, counts, strict=True) if int(count)}


def restart_plan() -> tuple[list[str], list[str]]:
    busy = busy_queues()
    ready, held = [], []
    for service, queues in SERVICE_QUEUES:
        unsafe = bool(queues) and (busy is None or bool(busy & set(queues)))
        (held if unsafe else ready).append(service)
    return ready, held


def advice_for(ready: list[str], held: list[str]) -> str:
    advice = RESTART_FIRST.format(services=" ".join(ready))
    return advice + NOT_NOW.format(services=" ".join(held)) if held else advice


def restarted_services(statement: Statement) -> list[str]:
    words = statement.words
    if statement.program == "docker":
        if words[1:2] != ["compose"]:
            return []
        rest = words[2:]
    elif statement.program == "docker-compose":
        rest = words[1:]
    else:
        return []
    index = 0
    while index < len(rest) and rest[index] not in COMPOSE_VERBS:
        index += 2 if rest[index] in COMPOSE_VALUE_FLAGS else 1
    if index >= len(rest):
        return []
    named, position = [], index + 1
    while position < len(rest):
        word = rest[position]
        if word.startswith("-"):
            position += 2 if word in COMPOSE_VALUE_FLAGS else 1
        else:
            named.append(word)
            position += 1
    if rest[index] == "up" and not {"-d", "--detach"} & set(rest):
        return []
    return named or [service for service, _ in SERVICE_QUEUES]


def succeeded(event: HookEvent) -> bool:
    if event.hook_event_name != "PostToolUse":
        return False
    response = (event.model_extra or {}).get("tool_response")
    if not isinstance(response, dict):
        return True
    codes = [response.get(key) for key in ("exitCode", "exit_code", "returncode")]
    return all(code in (None, 0) for code in codes) and not response.get("interrupted")


def mark_restarts(event: HookEvent, markers: Markers) -> bool:
    if event.tool_name not in SHELLS or not succeeded(event):
        return False
    services = {
        service
        for statement in shell_statements(event)
        for service in restarted_services(statement)
        if service in SERVICE_NAMES
    }
    for service in services:
        markers.set(f"{RESTARTED}{service}")
    if services:
        markers.set("workers-restarted")
    return bool(services)


def changed_worker_code(event: HookEvent) -> bool:
    if event.tool_name in EDITS:
        return bool(WORKER_CODE.search(event.relative))
    if event.tool_name not in SHELLS or not SCRIPTED_WRITE.search(event.command):
        return False
    try:
        changed = changed_by_this_call(event)
    except (
        OSError,
        subprocess.SubprocessError,
    ):  # silent: check() answers with UNCHECKED
        raise UncheckedWriteError from None
    return any(WORKER_CODE.search(f.path) for f in changed)


def check(event: HookEvent) -> Verdict | None:
    markers = Markers.main(event)
    if event.hook_event_name in AFTER_A_CALL:
        mark_restarts(event, markers)
        try:
            code_changed = changed_worker_code(event)
        except UncheckedWriteError:
            return context(NAME, UNCHECKED)
        if code_changed:
            note_due = markers.since("restart-noted") <= markers.since(
                "workers-restarted"
            )
            markers.set("worker-code")
            ready, held = restart_plan() if note_due else ([], [])
            if ready:
                markers.set("restart-noted")
                return context(NAME, advice_for(ready, held))
        return None
    changed = markers.since("worker-code")
    if (
        event.agent_id
        or event.stop_hook_active
        or not changed
        or markers.since("restart-asked") > changed
    ):
        return None
    ready, held = restart_plan()
    pending = [s for s in ready if markers.since(f"{RESTARTED}{s}") <= changed]
    if not pending:
        return None
    markers.set("restart-asked")
    return block(NAME, advice_for(pending, held))


GUARD = Guard(
    name=NAME,
    events=frozenset({*AFTER_A_CALL, "Stop", "SubagentStop"}),
    tools=frozenset({*EDITS, *SHELLS}),
    check=check,
)
