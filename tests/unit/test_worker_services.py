from typing import Any
lazy from pathlib import Path

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
    return {name for name, s in _compose().services.items() if s.build == "."}


def _worker_queues() -> dict[str, set[str]]:
    found = {}
    for name, service in _compose().services.items():
        cmd = service.command
        if "ofmhelpers.background.worker" in cmd:
            rest = cmd[cmd.index("ofmhelpers.background.worker") + 1 :]
            found[name] = set(rest) or {QueueName.DEFAULT.value}
    return found


def _compose_calls(path: Path, verb: str) -> list[set[str]]:
    calls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        _, found, rest = line.partition(f"$compose {verb} ")
        if found:
            words = rest.split(";")[0].split()
            calls.append({w for w in words if not w.startswith("-")})
    return calls


def test_every_queue_is_served_by_one_worker_service():
    assert _worker_queues() == {
        "worker": {"default", "clone-links", "events"},
        "preview-worker": {"previews"},
        "media-worker": {"media"},
        "edit-worker": {"edit"},
    }
    served = [q for queues in _worker_queues().values() for q in queues]
    assert sorted(served) == sorted(q.value for q in QueueName)


def test_the_edit_worker_has_three_cpus_and_two_gb():
    edit = _compose().services["edit-worker"].model_dump()
    assert (edit["cpus"], edit["mem_limit"]) == (3.0, "2g")


def test_a_worker_finishes_its_job_on_compose_stop():
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
    workers = set(_worker_queues())
    assert workers == WORKERS
    assert _compose_calls(path, "stop") == [workers | {"sweeps"}, {"ofmhelpers"}]


def test_the_sweeps_have_one_service_of_their_own():
    services = _compose().services
    running = [
        name
        for name, s in services.items()
        if "ofmhelpers.background.sweeps" in s.command
    ]
    assert running == ["sweeps"]
    assert services["sweeps"].command == [
        "python",
        "-m",
        "ofmhelpers.background.sweeps",
    ]
    assert services["sweeps"].stop_grace_period == "30s"


def test_a_worker_service_can_be_scaled():
    services = _compose().services
    named = {name for name in (*WORKERS, "sweeps") if services[name].container_name}
    assert named == set()


def test_every_app_service_runs_the_release_image():
    images = {_compose().services[name].image for name in _app_services()}
    assert images == {"ofmhelpers-app:${OFM_RELEASE:-dev}"}


@pytest.mark.parametrize("path", DEPLOYS, ids=lambda p: p.name)
def test_both_deploys_keep_the_previous_release_image(path: Path):
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
    inputs = workflow[True]["workflow_dispatch"]["inputs"]
    assert inputs["sha"]["required"] is False


def test_the_database_and_redis_passwords_are_required_never_a_default():
    services = _compose().services
    postgres = services["postgres"].model_dump()["environment"]
    app = services["ofmhelpers"].model_dump()["environment"]
    redis = services["redis"].model_dump()
    required_pg = "${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}"
    required_redis = "${REDIS_PASSWORD:?set REDIS_PASSWORD in .env}"
    assert f"POSTGRES_PASSWORD={required_pg}" in postgres
    assert (
        f"OFM_DATABASE_URL=postgresql+psycopg://ofmhelpers:{required_pg}"
        "@postgres:5432/ofmhelpers"
    ) in app
    assert f"OFM_REDIS_URL=redis://:{required_redis}@redis:6379/0" in app
    assert redis["command"][-2:] == ["--requirepass", required_redis]
    whole = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert ":-ofmhelpers}" not in whole


def test_both_deploys_set_the_database_password_from_env_after_the_app_stops():
    for deploy in DEPLOYS:
        text = deploy.read_text(encoding="utf-8")
        stop_app = text.index("stop ofmhelpers")
        backing = text.index("up -d --wait postgres redis")
        password = text.index("sh -s < scripts/postgres_password.sh")
        start = text.index("up -d --no-deps --force-recreate migrate")
        assert stop_app < backing < password < start
        assert "up -d --remove-orphans pot-provider" in text
        assert "postgres pot-provider" not in text


def test_no_image_floats_on_a_tag_alone():
    images = [
        s.image for s in _compose().services.values() if s.image and s.build is None
    ]
    floating = [i for i in images if i.endswith(":latest") or ":" not in i]
    assert floating == []
    assert any("bgutil-ytdlp-pot-provider:latest@sha256:" in i for i in images)


def test_dev_says_it_is_development_and_puts_nginx_in_front():
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
    prunes = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if "builder prune" in line and not line.strip().startswith("#")
    ]
    assert prunes == ["docker builder prune -af --filter until=24h >/dev/null"]


def _mount_of(path: str, volumes: list[str]) -> str:
    targets = [volume.split(":")[1] for volume in volumes]
    return max((t for t in targets if path.startswith(f"{t}/")), key=len)


@pytest.mark.parametrize("name", ["ofmhelpers", *sorted(WORKERS)])
def test_results_land_in_the_asset_stores_mount(name: str):
    service = _compose().services[name].model_dump()
    env = dict(line.split("=", 1) for line in service["environment"])

    out = _mount_of(f"{env['OFM_KIEAI_OUT_DIR']}/x", service["volumes"])

    assert out == _mount_of("/app/uploads/assets/x", service["volumes"])


@pytest.mark.parametrize("path", DEPLOYS, ids=lambda p: p.name)
def test_both_deploys_fail_when_the_sweeps_or_a_worker_did_not_stay_up(path: Path):
    text = path.read_text(encoding="utf-8")
    services = "sweeps worker preview-worker media-worker edit-worker"
    assert f"for service in {services}; do" in text
    assert '[ "$state" != "running 0" ]' in text
    assert set(services.split()) == WORKERS | {"sweeps"}


@pytest.mark.parametrize("path", DEPLOYS, ids=lambda p: p.name)
def test_both_deploys_keep_the_image_of_the_one_running_app_container(path: Path):
    text = path.read_text(encoding="utf-8")
    assert "running=$($compose ps -q ofmhelpers | head -n 1)" in text
    assert "ps -aq ofmhelpers" not in text
