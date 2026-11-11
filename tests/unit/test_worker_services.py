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
WORKERS = {"worker", "media-worker", "edit-worker", "preview-worker"}


class Service(BaseModel):
    model_config = ConfigDict(extra="allow")

    build: Any = None
    image: str | None = None
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
    assert app >= {"ofmhelpers", "migrate", "sweeps", *WORKERS}
    assert _compose_calls(path, "build") == [app]
    assert _compose_calls(path, "up -d --no-deps --force-recreate") == [app]


@pytest.mark.parametrize("path", DEPLOYS, ids=lambda p: p.name)
def test_both_deploys_stop_the_workers_before_the_app(path: Path):
    """The workers drain (up to their grace period) while the app still
    serves; only then does the app stop for the migration."""
    workers = set(_worker_queues())
    assert workers == WORKERS
    assert _compose_calls(path, "stop") == [workers | {"sweeps"}, {"ofmhelpers"}]


def test_the_sweeps_have_one_service_of_their_own():
    """Their own process (`ofmhelpers/sweeps.py`), never the web app's: a
    second web process would run every sweep twice. Time to finish the
    Telegram read on `compose stop` (OFM_TELEGRAM_ASK_WAIT_S, 10 s)."""
    services = _compose().services
    running = [name for name, s in services.items() if "ofmhelpers.sweeps" in s.command]
    assert running == ["sweeps"]
    assert services["sweeps"].command == ["python", "-m", "ofmhelpers.sweeps"]
    assert services["sweeps"].stop_grace_period == "30s"


def test_a_worker_service_can_be_scaled():
    """A fixed `container_name` refuses `--scale` (two containers, one name).
    More load = more worker processes or containers per queue."""
    services = _compose().services
    named = {name for name in (*WORKERS, "sweeps") if services[name].container_name}
    assert named == set()


def test_every_app_service_runs_the_release_image():
    """One image, named by the commit it was built from (OFM_RELEASE, set by
    both deploys): the build is the artifact, and the one before is kept."""
    images = {_compose().services[name].image for name in _app_services()}
    assert images == {"ofmhelpers-app:${OFM_RELEASE:-dev}"}


@pytest.mark.parametrize("path", DEPLOYS, ids=lambda p: p.name)
def test_both_deploys_keep_the_previous_release_image(path: Path):
    """The server builds the image, so the one it replaces is the only way
    back without a rebuild: it is tagged with its commit, the deploy reuses a
    commit's image when it is still there, and the clean-up spares the
    release it replaced."""
    text = path.read_text(encoding="utf-8")
    assert 'release="ofmhelpers-app:$OFM_RELEASE"' in text
    assert 'docker image inspect "$release" >/dev/null 2>&1 || $compose build' in text
    assert (
        '[ "$image" = "$release" ] || [ "$image" = "$previous" ] || docker image rm'
        in text
    )
    assert "docker image prune -af" not in text
    assert "docker system prune" not in text


@pytest.mark.parametrize("path", DEPLOYS, ids=lambda p: p.name)
def test_both_deploys_take_only_a_commit_of_main(path: Path):
    text = path.read_text(encoding="utf-8")
    assert 'git merge-base --is-ancestor "$target" origin/main' in text
    assert 'git reset --hard "$target"' in text


def test_the_manual_deploy_never_commits_or_pushes():
    """Until 2026-11-11 it ran `git add -A`, committed, pushed main and
    deployed, with no CI in between."""
    code = [
        line.strip()
        for line in DEPLOYS[0].read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    ]
    assert [
        line for line in code if line.startswith(("git add", "git commit", "git push"))
    ] == []
    assert 'if [ -n "$(git status --porcelain)" ]; then' in code
    assert (
        'if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then' in code
    )


def test_the_deploy_workflow_takes_a_sha_by_hand():
    workflow = yaml.safe_load(DEPLOYS[1].read_text(encoding="utf-8"))
    # PyYAML reads the key `on` as True.
    inputs = workflow[True]["workflow_dispatch"]["inputs"]
    assert inputs["sha"]["required"] is False


def test_the_database_password_comes_from_the_environment():
    """Not written into the file: POSTGRES_PASSWORD, with the value every
    existing volume was made with as the fallback (the server sets none)."""
    services = _compose().services
    postgres = services["postgres"].model_dump()["environment"]
    app = services["ofmhelpers"].model_dump()["environment"]
    assert "POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-ofmhelpers}" in postgres
    assert (
        "OFM_DATABASE_URL=postgresql+psycopg://ofmhelpers:"
        "${POSTGRES_PASSWORD:-ofmhelpers}@postgres:5432/ofmhelpers"
    ) in app


def test_no_image_floats_on_a_tag_alone():
    """`:latest` is a different build every pull: dev, CI and a rebuilt
    server would each run their own."""
    images = [
        s.image for s in _compose().services.values() if s.image and s.build is None
    ]
    floating = [i for i in images if i.endswith(":latest") or ":" not in i]
    assert floating == []
    assert any("bgutil-ytdlp-pot-provider:latest@sha256:" in i for i in images)


def test_dev_says_it_is_development_and_puts_nginx_in_front():
    """OFM_ENV has no default: the overlay names it on every app service.
    nginx there uses the site file the server does (nginx/ofmhelpers.conf)."""
    text = (REPO / "docker-compose.dev.yml").read_text(encoding="utf-8")
    dev = yaml.safe_load(text)["services"]
    for name in (*_app_services(), "tests"):
        assert "OFM_ENV=development" in dev[name]["environment"], name
    assert (
        "./nginx/ofmhelpers.conf:/etc/nginx/conf.d/default.conf:ro"
        in (dev["nginx"]["volumes"])
    )
    site = (REPO / "nginx" / "ofmhelpers.conf").read_text(encoding="utf-8")
    assert "client_max_body_size 2048m;" in site
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in site
    assert "proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;" in site
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "nginx/ofmhelpers.conf" in readme
    assert "proxy_pass" not in readme


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


@pytest.mark.parametrize("path", DEPLOYS, ids=lambda p: p.name)
def test_both_deploys_fail_when_the_sweeps_or_a_worker_did_not_stay_up(path: Path):
    """`/health` is the web app only (Kimi review 2026-11-11): a sweeps or
    worker container crashing at its start would be a green deploy with
    recovery, the scans and every queue dead."""
    text = path.read_text(encoding="utf-8")
    services = "sweeps worker preview-worker media-worker edit-worker"
    assert f"for service in {services}; do" in text
    assert '[ "$state" != "running 0" ]' in text
    assert set(services.split()) == WORKERS | {"sweeps"}


@pytest.mark.parametrize("path", DEPLOYS, ids=lambda p: p.name)
def test_both_deploys_keep_the_image_of_the_one_running_app_container(path: Path):
    """`ps -a` also lists stopped and one-off containers: two ids made
    `previous` two lines, it matched no image, and the kept release was removed."""
    text = path.read_text(encoding="utf-8")
    assert "running=$($compose ps -q ofmhelpers | head -n 1)" in text
    assert "ps -aq ofmhelpers" not in text
