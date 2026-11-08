"""The worker containers (docker-compose.yml) and the two deploy paths
(scripts/deploy.sh, .github/workflows/deploy.yml) stay in step: every queue
`ofmhelpers.worker` serves has its service, both deploys build, stop and
start every app service, and a worker gets time to finish its job on
`compose stop`. A service left off one deploy keeps running old code."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel, ConfigDict
from repo_paths import REPO

from ofmhelpers.cache import QueueName

DEPLOYS = [
    REPO / "scripts" / "deploy.sh",
    REPO / ".github" / "workflows" / "deploy.yml",
]
WORKERS = {
    "worker",
    "clone-links-worker",
    "media-worker",
    "edit-worker",
    "events-worker",
}


class Service(BaseModel):
    model_config = ConfigDict(extra="allow")

    build: Any = None
    container_name: str | None = None
    command: list[str] = []
    stop_grace_period: str | None = None


class Compose(BaseModel):
    services: dict[str, Service]


def _compose() -> Compose:
    text = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    return Compose.model_validate(yaml.safe_load(text))


def _app_services() -> set[str]:
    """The services built from this repo's image."""
    return {name for name, s in _compose().services.items() if s.build == "."}


def _worker_queues() -> dict[str, str]:
    """service -> the queue its `python -m ofmhelpers.worker [queue]` serves."""
    found = {}
    for name, service in _compose().services.items():
        cmd = service.command
        if "ofmhelpers.worker" in cmd:
            rest = cmd[cmd.index("ofmhelpers.worker") + 1 :]
            found[name] = rest[0] if rest else QueueName.DEFAULT
    return found


def _compose_calls(path: Path, verb: str) -> list[set[str]]:
    """The services each `$compose <verb> ...` line of a deploy names, in
    order (flags and anything after `;` left out)."""
    calls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        _, found, rest = line.partition(f"$compose {verb} ")
        if found:
            words = rest.split(";")[0].split()
            calls.append({w for w in words if not w.startswith("-")})
    return calls


def test_every_queue_has_its_own_worker_service():
    assert _worker_queues() == {
        "worker": QueueName.DEFAULT,
        "clone-links-worker": QueueName.CLONE_LINKS,
        "preview-worker": QueueName.PREVIEWS,
        "media-worker": QueueName.MEDIA,
        "edit-worker": QueueName.EDIT,
        "events-worker": QueueName.EVENTS,
    }


def test_the_clone_links_worker_is_the_worker_on_its_own_queue():
    """Same image, env, mounts, network, restart and shutdown as `worker`:
    only its name and queue differ."""
    services = _compose().services
    differ = {"container_name", "command"}
    assert services["clone-links-worker"].model_dump(exclude=differ) == services[
        "worker"
    ].model_dump(exclude=differ)


def test_the_edit_worker_has_three_cpus_and_two_gb():
    """A 14 s 1440p 60 fps cut: 15 s at 1.5 CPUs, 7 s at 3 (2026-11-06); the
    RAM cap keeps a runaway ffmpeg in its own container."""
    edit = _compose().services["edit-worker"].model_dump()
    assert (edit["cpus"], edit["mem_limit"]) == (3.0, "2g")


def test_a_worker_finishes_its_job_on_compose_stop():
    """Docker's default 10 s SIGKILLed /clone-links reviews mid-LLM call or
    mid-upload on every deploy (review #4)."""
    services = _compose().services
    assert {
        name: services[name].stop_grace_period for name in WORKERS
    } == dict.fromkeys(WORKERS, "120s")


@pytest.mark.parametrize("path", DEPLOYS, ids=lambda p: p.name)
def test_both_deploys_build_and_start_every_app_service(path: Path):
    app = _app_services()
    assert app >= {"ofmhelpers", "migrate", "preview-worker", *WORKERS}
    assert _compose_calls(path, "build") == [app]
    assert _compose_calls(path, "up -d --no-deps --force-recreate") == [app]


@pytest.mark.parametrize("path", DEPLOYS, ids=lambda p: p.name)
def test_both_deploys_stop_the_workers_before_the_app(path: Path):
    """The workers drain (up to their grace period) while the app still
    serves; only then does the app stop for the migration."""
    workers = set(_worker_queues())
    assert _compose_calls(path, "stop") == [workers, {"ofmhelpers"}]


@pytest.mark.parametrize("path", DEPLOYS, ids=lambda p: p.name)
def test_both_deploys_keep_only_a_days_build_cache(path: Path):
    """Prod 2026-11-08: `--max-used-space 5gb` left 8 GB of build cache on
    Docker 29, the disk at 93%."""
    prunes = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if "builder prune" in line and not line.strip().startswith("#")
    ]
    assert prunes == ["docker builder prune -af --filter until=24h >/dev/null"]
