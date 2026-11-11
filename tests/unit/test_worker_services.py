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
WORKERS = {"worker", "media-worker", "edit-worker"}


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


def _worker_queues() -> dict[str, set[str]]:
    """service -> the queues its `python -m ofmhelpers.worker [queue ...]`
    serves."""
    found = {}
    for name, service in _compose().services.items():
        cmd = service.command
        if "ofmhelpers.worker" in cmd:
            rest = cmd[cmd.index("ofmhelpers.worker") + 1 :]
            found[name] = set(rest) or {QueueName.DEFAULT.value}
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


def test_every_queue_is_served_by_one_worker_service():
    """The network queues share `worker` (a pool each, `worker.QueueGroup`);
    what encodes has a service of its own, for its CPU and memory limits. A
    queue no service names would fill up with nobody taking from it."""
    assert _worker_queues() == {
        "worker": {"default", "clone-links", "events"},
        "preview-worker": {"previews"},
        "media-worker": {"media"},
        "edit-worker": {"edit"},
    }
    served = [q for queues in _worker_queues().values() for q in queues]
    assert sorted(served) == sorted(q.value for q in QueueName)


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


def _mount_of(path: str, volumes: list[str]) -> str:
    """The container path of the mount `path` is in: the longest one."""
    targets = [volume.split(":")[1] for volume in volumes]
    return max((t for t in targets if path.startswith(f"{t}/")), key=len)


@pytest.mark.parametrize("name", ["ofmhelpers", *sorted(WORKERS)])
def test_results_land_in_the_asset_stores_mount(name: str):
    """The asset store hard-links each result (`register_generated_asset`);
    across two mounts the link fails and it copied: every result twice,
    17.5 GB of prod's 72 GB disk (2026-11-11)."""
    service = _compose().services[name].model_dump()
    env = dict(line.split("=", 1) for line in service["environment"])

    out = _mount_of(f"{env['OFM_KIEAI_OUT_DIR']}/x", service["volumes"])

    assert out == _mount_of("/app/uploads/assets/x", service["volumes"])
