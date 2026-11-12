"""Which key a generation runs on (`features/account/keys.py`): a person's
saved key of the model's slot, a key saved under the wrong slot passed over,
the first working key kept as theirs, and its end recorded on the job."""

import os

os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient
from signin import make_user, sign_in

from ofmhelpers.db.stores import jobs, users
from ofmhelpers.features.account.keys import Keys, keep_used
from ofmhelpers.features.jobs import runner
from ofmhelpers.integrations.ai_providers import tasks
from ofmhelpers.integrations.ai_providers.apiyi.fake import FAKE_KEY, fake_apiyi
from ofmhelpers.integrations.ai_providers.apiyi.provider import ApiyiProvider
from ofmhelpers.schemas import Role
from ofmhelpers.web.main import app

WAN = "apiyi:wan3.0-video"
SEEDREAM = "apiyi:seedream-4-5-251128"
SEEDANCE = "apiyi:doubao-seedance-2-0-260128"
# One key per APIYI token group, as `GET /v1/models` tells them apart.
WAN_KEY = "sk-wanGroupKey0000000000000000000000000000aaaa"
DEFAULT_KEY = "sk-defaultGroupKey00000000000000000000000bbbb"
SEEDANCE_KEY = "sk-seedanceGroupKey0000000000000000000000cccc"
OTHER_WAN_KEY = "sk-anotherWanGroupKey00000000000000000000dddd"


@pytest.fixture
def groups(fake_externals):
    """APIYI answering each group's key with the models that group reaches."""
    del fake_externals
    fake_apiyi().key_models.update(
        {
            WAN_KEY: ["wan3.0-video", "wan2.7-t2v", "happyhorse-1.1-r2v"],
            OTHER_WAN_KEY: ["wan3.0-video", "happyhorse-1.1-r2v"],
            DEFAULT_KEY: ["seedream-4-5-251128", "gemini-3-pro-image", "veo-3.1"],
            SEEDANCE_KEY: ["doubao-seedance-2-0-260128"],
        }
    )


def _user(email: str = "keys@example.com", role: Role = Role.USER) -> int:
    return make_user(email, role).id


def _saved(user_id: int) -> dict[str, str]:
    return {k.provider: k.api_key for k in users.api_keys(user_id).keys}


def test_account_lists_one_apiyi_key_per_token_group():
    client = TestClient(app)
    user = sign_in(client)

    page = client.get("/account").text
    for field in ("key_apiyi", "key_apiyi-wan", "key_apiyi-seedance"):
        assert f'name="{field}"' in page

    r = client.post(
        "/account/keys", data={"key_apiyi": DEFAULT_KEY, "key_apiyi-wan": WAN_KEY}
    )

    assert "APIYI and APIYI Wan &amp; HappyHorse keys saved." in r.text
    assert _saved(user.id) == {"apiyi": DEFAULT_KEY, "apiyi-wan": WAN_KEY}


@pytest.mark.parametrize(
    ("model", "used"),
    [(WAN, WAN_KEY), (SEEDREAM, DEFAULT_KEY), (SEEDANCE, SEEDANCE_KEY)],
)
@pytest.mark.usefixtures("groups")
def test_a_run_goes_out_on_the_saved_key_of_its_models_group(model, used):
    user_id = _user()
    users.set_api_key(user_id, "apiyi", DEFAULT_KEY)
    users.set_api_key(user_id, "apiyi-wan", WAN_KEY)
    users.set_api_key(user_id, "apiyi-seedance", SEEDANCE_KEY)
    keys = Keys.of(user_id)

    # The form shows one of them (the default slot's), whatever the model.
    assert keys.prefill("apiyi") == DEFAULT_KEY
    assert keys.for_run("apiyi", model, DEFAULT_KEY) == used
    assert keys.for_run("apiyi", model, "") == used


@pytest.mark.usefixtures("groups")
def test_a_group_without_a_saved_key_falls_back_to_the_default_one():
    user_id = _user()
    users.set_api_key(user_id, "apiyi", DEFAULT_KEY)

    assert Keys.of(user_id).for_run("apiyi", SEEDANCE, "") == DEFAULT_KEY


@pytest.mark.usefixtures("groups")
def test_a_key_saved_under_the_wrong_group_is_passed_over(monkeypatch):
    """Prod 2026-11-11, job d66e133c: the admin's saved APIYI key was a
    Wan-group key, the three group keys were in .env, and every Seedream run
    went out on the saved one: 503, shown as "Wrong API key"."""
    monkeypatch.setenv("APIYI_API_KEY_WAN", WAN_KEY)
    monkeypatch.setenv("APIYI_API_KEY_SEEDANCE", SEEDANCE_KEY)
    monkeypatch.setenv("APIYI_API_KEY_DEFAULT", DEFAULT_KEY)
    admin_id = _user("admin-keys@example.com", Role.ADMIN)
    users.set_api_key(admin_id, "apiyi", OTHER_WAN_KEY)
    keys = Keys.of(admin_id)
    prefilled = keys.prefill("apiyi")

    assert prefilled == OTHER_WAN_KEY
    assert keys.for_run("apiyi", SEEDREAM, prefilled) == DEFAULT_KEY
    assert keys.for_run("apiyi", SEEDANCE, prefilled) == SEEDANCE_KEY
    # Their own key still runs what its group reaches.
    assert keys.for_run("apiyi", WAN, prefilled) == OTHER_WAN_KEY


@pytest.mark.usefixtures("groups")
def test_a_user_never_gets_an_env_key(monkeypatch):
    monkeypatch.setenv("APIYI_API_KEY_DEFAULT", DEFAULT_KEY)
    monkeypatch.setenv("APIYI_API_KEY_WAN", WAN_KEY)
    user_id = _user()
    users.set_api_key(user_id, "apiyi", OTHER_WAN_KEY)
    keys = Keys.of(user_id)

    assert keys.for_run("apiyi", SEEDREAM, "") == OTHER_WAN_KEY
    assert Keys.of(_user("none@example.com")).for_run("apiyi", SEEDREAM, "") == ""


@pytest.mark.usefixtures("groups")
def test_a_typed_key_that_is_not_theirs_runs_as_typed():
    user_id = _user()
    users.set_api_key(user_id, "apiyi", DEFAULT_KEY)

    assert Keys.of(user_id).for_run("apiyi", SEEDREAM, " sk-typed ") == "sk-typed"
    # Nothing was asked of APIYI about a key that runs as typed.
    assert fake_apiyi().model_lists == 0


@pytest.mark.usefixtures("groups")
def test_apiyi_not_answering_leaves_the_saved_order():
    """An unknown key (401 on the model list) cannot be placed: the slot's
    own key is used and the run itself says what is wrong with it."""
    user_id = _user()
    users.set_api_key(user_id, "apiyi", "sk-unknownToApiyi")

    assert ApiyiProvider.slot_of_key("sk-unknownToApiyi") is None
    assert Keys.of(user_id).for_run("apiyi", SEEDREAM, "") == "sk-unknownToApiyi"


@pytest.mark.usefixtures("groups")
def test_the_fake_key_is_a_wan_group_key():
    assert ApiyiProvider.slot_of_key(FAKE_KEY) == "apiyi-wan"


def _task(job_id: str, model: str, api_key: str, provider: str = "apiyi"):
    return tasks.ProviderTask(
        job_id=job_id, provider=provider, model=model, task_id="t-1", api_key=api_key
    )


def test_the_first_key_that_worked_is_kept_under_its_models_slot(monkeypatch):
    monkeypatch.setenv("KIE_AI_API_KEY_ADMIN", "env-kie-key")
    user_id = _user()

    keep_used(user_id, [_task("j1", WAN, WAN_KEY)])
    assert _saved(user_id) == {"apiyi-wan": WAN_KEY}

    # A saved slot is never replaced by a run, another slot is filled.
    keep_used(
        user_id,
        [_task("j2", WAN, OTHER_WAN_KEY), _task("j2", SEEDREAM, DEFAULT_KEY)],
    )
    assert _saved(user_id) == {"apiyi-wan": WAN_KEY, "apiyi": DEFAULT_KEY}

    # The admin's .env key stays in .env.
    keep_used(user_id, [_task("j3", "kie:nano-banana-pro", "env-kie-key", "kie")])
    assert "kie" not in _saved(user_id)


def test_a_finished_run_keeps_its_key_and_records_its_end():
    user_id = _user()

    def run(job_id: str) -> list:
        tasks.remember(_task(job_id, WAN, WAN_KEY))
        return []

    job_id = runner.start_job("ai", {"model": WAN}, run, {}, actor=str(user_id))

    job = jobs.get_job(job_id)
    assert job is not None
    assert job.status == "done"
    assert [g.key_tail for g in job.meta.generations] == [WAN_KEY[-4:]]
    assert WAN_KEY not in job.model_dump_json()
    assert _saved(user_id) == {"apiyi-wan": WAN_KEY}


def test_a_failed_run_keeps_no_key():
    user_id = _user()

    def run(job_id: str) -> list:
        tasks.remember(_task(job_id, WAN, WAN_KEY))
        msg = "the export failed"
        raise RuntimeError(msg)

    job_id = runner.start_job("ai", {"model": WAN}, run, {}, actor=str(user_id))

    job = jobs.get_job(job_id)
    assert job is not None
    assert job.status == "failed"
    assert _saved(user_id) == {}
