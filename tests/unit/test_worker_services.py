import ast
import re
from typing import Any
lazy from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings
from repo_paths import REPO

from ofmhelpers.cache import QueueName
from ofmhelpers.config import settings

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
    app_lines = [f"{key}={value}" for key, value in app.items()]
    redis = services["redis"].model_dump()
    required_pg = "${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}"
    required_redis = "${REDIS_PASSWORD:?set REDIS_PASSWORD in .env}"
    assert f"POSTGRES_PASSWORD={required_pg}" in postgres
    assert (
        f"OFM_DATABASE_URL=postgresql+psycopg://ofmhelpers:{required_pg}"
        "@postgres:5432/ofmhelpers"
    ) in app_lines
    assert f"OFM_REDIS_URL=redis://:{required_redis}@redis:6379/0" in app_lines
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
    env = service["environment"]

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


def _env_names(cls: type[BaseSettings], fields: set[str] | None = None) -> set[str]:
    names = set()
    prefix = cls.model_config.get("env_prefix") or ""
    for field, info in cls.model_fields.items():
        if fields is not None and field not in fields:
            continue
        alias = info.validation_alias
        if alias is None:
            names.add((prefix + field).upper())
        elif isinstance(alias, str):
            names.add(alias)
        else:
            names.update(alias.choices)
    return names


def _settings_env_names() -> set[str]:
    names = set()
    pending = list(BaseSettings.__subclasses__())
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        if cls.__module__ == "ofmhelpers.config.settings":
            names |= _env_names(cls)
    return names


SOURCE = REPO / "src"
EDIT_AND_PREVIEW_TASKS = [
    "ofmhelpers.features.edit.service",
    "ofmhelpers.features.edit.cleanup",
    "ofmhelpers.features.edit.clips",
    "ofmhelpers.features.previews.render",
    "ofmhelpers.features.previews.backfill",
    "ofmhelpers.features.trials.cleanup",
    "ofmhelpers.features.downloads.cleanup",
    "ofmhelpers.features.viral.cleanup",
    "ofmhelpers.features.jobs.raw_files",
]


def _module_file(module: str) -> Path | None:
    base = SOURCE.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def _imported_modules(tree: ast.AST) -> set[str]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "ofmhelpers"
        ):
            found.add(node.module or "")
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.startswith("ofmhelpers"))
    return found


def _settings_reads(tree: ast.AST) -> set[tuple[str, str]]:
    return {
        (node.value.attr, node.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "settings"
    }


SECRET_NAME = re.compile(
    r"(KEYS?|SECRET|TOKEN|PASSWORD|DSN|PRIVATE_KEY|SERVICE_ACCOUNT)"
    r"(_ADMIN|_DEFAULT|_WAN|_SEEDANCE)?$"
)
TELEMETRY_THE_MEDIA_SERVICES_MAY_HOLD = {"LOGFIRE_TOKEN", "SENTRY_DSN"}
WITHHELD_FROM_THE_MEDIA_SERVICES = {
    "APP_PASSWORD_ADMIN",
    "CEREBRAS_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "OFM_APPLE_PRIVATE_KEY",
    "OFM_GDRIVE_SERVICE_ACCOUNT",
    "OFM_GOOGLE_CLIENT_SECRET",
    "OFM_PASSWORD_ADMIN",
    "OFM_TELEGRAM_BOT_TOKEN",
    "OFM_VAULT_KEY",
    "SESSION_SECRET",
}
URLS_WITH_A_PASSWORD = {"OFM_DATABASE_URL", "OFM_REDIS_URL"}


def _import_closure(entries: list[str]) -> dict[str, ast.AST]:
    trees: dict[str, ast.AST] = {}
    pending = list(entries)
    while pending:
        module = pending.pop()
        if module in trees:
            continue
        file = _module_file(module)
        if file is None:
            continue
        trees[module] = ast.parse(file.read_text(encoding="utf-8"))
        parent, _, _ = module.rpartition(".")
        pending.extend(_imported_modules(trees[module]) | ({parent} - {""}))
    return trees


def _subclasses(cls: type) -> list[type]:
    return [
        found
        for direct in cls.__subclasses__()
        for found in (direct, *_subclasses(direct))
    ]


def _group_classes() -> dict[str, type[BaseSettings]]:
    classes = {
        cls.__name__: cls
        for cls in _subclasses(BaseSettings)
        if cls.__module__ == "ofmhelpers.config.settings"
    }
    groups = REPO / "src" / "ofmhelpers" / "config" / "__init__.py"
    return {
        node.name: classes[node.returns.id]
        for node in ast.walk(ast.parse(groups.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef)
        and isinstance(node.returns, ast.Name)
        and node.returns.id in classes
        and any(
            isinstance(d, ast.Name) and d.id == "property" for d in node.decorator_list
        )
    }


def _env_names_read(trees: dict[str, ast.AST]) -> set[str]:
    names = set()
    group_classes = _group_classes()
    for group, attr in set().union(*map(_settings_reads, trees.values())):
        cls = group_classes[group]
        required = {f for f, info in cls.model_fields.items() if info.is_required()}
        fields = {attr} | required if attr in cls.model_fields else None
        names |= _env_names(cls, fields)
    return names


def _env_names_read_by_tasks() -> set[str]:
    trees = _import_closure(EDIT_AND_PREVIEW_TASKS)
    assert set(EDIT_AND_PREVIEW_TASKS) <= set(trees)
    return _env_names_read(trees)


MIGRATIONS = [
    REPO / "alembic" / "env.py",
    *(REPO / "alembic" / "versions").glob("*.py"),
]
WITHHELD_FROM_MIGRATE = {"OFM_VAULT_KEY", "LOGFIRE_TOKEN"}


def _env_names_read_by_migrations() -> set[str]:
    trees = {str(p): ast.parse(p.read_text(encoding="utf-8")) for p in MIGRATIONS}
    imported = set().union(*map(_imported_modules, trees.values()))
    return _env_names_read(trees | _import_closure(sorted(imported)))


def test_migrate_gets_every_setting_the_migrations_read_but_the_secrets():
    read = _env_names_read_by_migrations()
    assert read >= WITHHELD_FROM_MIGRATE
    assert read - WITHHELD_FROM_MIGRATE - set(_service_env("migrate")) == set()


def test_only_secrets_the_media_tasks_read_are_withheld_from_them():
    assert all(map(SECRET_NAME.search, WITHHELD_FROM_THE_MEDIA_SERVICES))
    assert _env_names_read_by_tasks() & WITHHELD_FROM_THE_MEDIA_SERVICES


def _service_env(name: str) -> dict[str, Any]:
    return _compose().services[name].model_dump()["environment"]


def test_the_services_that_may_hold_every_key_take_the_whole_env_file():
    with_file = {
        name
        for name, s in _compose().services.items()
        if s.model_dump().get("env_file")
    }
    assert with_file == {"ofmhelpers", "worker", "sweeps", "media-worker"}


@pytest.mark.parametrize("name", ["edit-worker", "preview-worker"])
def test_a_names_only_service_lists_every_setting_its_tasks_read(name: str):
    read = _env_names_read_by_tasks() - WITHHELD_FROM_THE_MEDIA_SERVICES
    assert read - set(_service_env(name)) == set()


@pytest.mark.parametrize("name", ["edit-worker", "preview-worker", "migrate"])
def test_a_service_without_the_env_file_gets_every_setting_with_no_default(
    name: str,
):
    infra = type(settings.infra)
    required = {
        field for field, info in infra.model_fields.items() if info.is_required()
    }
    assert required
    assert _env_names(infra, required) - set(_service_env(name)) == set()


@pytest.mark.parametrize("name", ["edit-worker", "preview-worker"])
def test_a_service_passes_in_only_names_settings_reads(name: str):
    passed = {key for key, value in _service_env(name).items() if value is None}
    assert passed
    assert passed <= _settings_env_names()


def test_the_services_that_run_media_hold_no_provider_key_or_vault_key():
    secrets = {
        "OFM_VAULT_KEY",
        "SESSION_SECRET",
        "KIE_AI_API_KEY_ADMIN",
        "HF_API_KEY_ADMIN",
        "GEMINI_API_KEY",
        "APIFY_API_KEYS",
        "OFM_GDRIVE_SERVICE_ACCOUNT",
        "OFM_TELEGRAM_BOT_TOKEN",
    }
    for name in ("edit-worker", "preview-worker", "migrate"):
        assert secrets.isdisjoint(_service_env(name)), name
    for name in ("edit-worker", "preview-worker"):
        env = _service_env(name)
        assert WITHHELD_FROM_THE_MEDIA_SERVICES.isdisjoint(env), name
        assert {k for k in env if SECRET_NAME.search(k)} == (
            TELEMETRY_THE_MEDIA_SERVICES_MAY_HOLD
        ), name
        assert {k for k, v in env.items() if "PASSWORD" in str(v)} == (
            URLS_WITH_A_PASSWORD
        ), name
    assert [k for k in _service_env("migrate") if SECRET_NAME.search(k)] == []


def test_the_rq_dashboard_and_the_tunnel_are_dev_only_and_on_loopback():
    base = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert "cloudflared" not in base.replace("# ", "")
    dev = yaml.safe_load((REPO / "docker-compose.dev.yml").read_text(encoding="utf-8"))
    dashboard = dev["services"]["rq-dashboard"]
    assert dashboard["ports"] == ["127.0.0.1:9181:9181"]
    assert dashboard["profiles"] == ["dev"]
