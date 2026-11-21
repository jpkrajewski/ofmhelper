import os
import shlex
import subprocess
from pathlib import Path

from guards.event import Guard, HookEvent, Verdict, context

NAME = "docker_up"
DOCKER_DESKTOP = Path(
    os.environ.get("CLAUDE_GUARDS_DOCKER_DESKTOP")
    or r"C:\Program Files\Docker\Docker\Docker Desktop.exe"
)
STARTED = (
    "Docker Desktop was down at session start, so it was started; the engine answers within "
    "seconds and the dev stack comes back by itself."
)
NOT_STARTED = (
    "Docker Desktop was down at session start and could not be started ({error}): "
    "start it by hand before any docker command."
)


def docker_cli() -> list[str]:
    return shlex.split(os.environ.get("CLAUDE_GUARDS_DOCKER") or "docker")


def engine_answers() -> bool:
    try:
        return (
            subprocess.run(
                [*docker_cli(), "info"], capture_output=True, timeout=8, check=False
            ).returncode
            == 0
        )
    except (
        OSError,
        subprocess.TimeoutExpired,
    ):  # silent: no CLI or no answer = engine down
        return False


def check(_event: HookEvent) -> Verdict | None:
    if engine_answers() or not DOCKER_DESKTOP.is_file():
        return None
    try:
        subprocess.Popen(
            [str(DOCKER_DESKTOP)],
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
    except OSError as error:
        return context(NAME, NOT_STARTED.format(error=error))
    return context(NAME, STARTED)


GUARD = Guard(name=NAME, events=frozenset({"SessionStart"}), check=check)
