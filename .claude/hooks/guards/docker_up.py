import subprocess
from pathlib import Path

from guards.event import Guard, HookEvent, Verdict, context

NAME = "docker_up"
DOCKER_DESKTOP = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
STARTED = (
    "Docker Desktop was down at session start, so it was started; the engine answers within "
    "seconds and the dev stack comes back by itself."
)


def engine_answers() -> bool:
    try:
        return (
            subprocess.run(
                ["docker", "info"], capture_output=True, timeout=8, check=False
            ).returncode
            == 0
        )
    except (
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ):  # silent: no CLI or no answer = engine down
        return False


def check(_event: HookEvent) -> Verdict | None:
    if engine_answers() or not DOCKER_DESKTOP.is_file():
        return None
    subprocess.Popen(
        [str(DOCKER_DESKTOP)], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
    )
    return context(NAME, STARTED)


GUARD = Guard(name=NAME, events=frozenset({"SessionStart"}), check=check)
