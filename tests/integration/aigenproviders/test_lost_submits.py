"""A submit whose answer is lost after the provider made the request (prod
2026-11-05: kie.ai made and billed a Wan 3.0 clip 2 s after our createTask
gave up; it was found only on kie.ai's site). kie.ai has no idempotency key,
so its callback names the task: the run goes on with it, or recovery once the
run gave up. Elyum takes our key, so its submit is simply sent again.

kie.ai at the wire (`responses`), its callback through the app's route."""

import base64
import hashlib
import hmac
import json
import os
import time
from collections.abc import Iterator

os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
import requests
import responses
from fastapi.testclient import TestClient
from repo_paths import FIXTURES
from responses import registries
from urllib3.exceptions import MaxRetryError, NewConnectionError

from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers import runner, self_heal, tasks
from ofmhelpers.integrations.ai_providers.catalog import get_spec
from ofmhelpers.integrations.ai_providers.elyum.fake import FAKE_KEY, fake_elyum
from ofmhelpers.integrations.ai_providers.elyum.provider import ElyumProvider
from ofmhelpers.integrations.ai_providers.errors import (
    ProviderInputError,
    ProviderUnavailableError,
)
from ofmhelpers.integrations.ai_providers.higgsfield.fake import FAKE_KEY as HF_KEY
from ofmhelpers.integrations.ai_providers.higgsfield.fake import fake_higgsfield
from ofmhelpers.integrations.ai_providers.higgsfield.provider import HiggsfieldProvider
from ofmhelpers.integrations.ai_providers.kie.provider import KieProvider
from ofmhelpers.schemas.errors import error_reason
from ofmhelpers.web.main import app

KIE_SPEC = get_spec("kie:seedream/5-lite-text-to-image")
TASK = "task_seedream_0001"
RESULT = "https://tempfile.aiquickdraw.com/results/task_seedream_0001.png"
PNG = b"\x89PNG\r\n\x1a\nfake"
BASE = "https://ascendglobal.test/"
HMAC_KEY = "kie-webhook-key"


@pytest.fixture(autouse=True)
def public_server(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", BASE)
    monkeypatch.delenv("OFM_KIEAI_WEBHOOK_HMAC_KEY", raising=False)
    monkeypatch.delenv("KIE_AI_WEBHOOK_HMAC_KEY", raising=False)
    monkeypatch.setenv("OFM_AIGEN_POLL_INTERVAL_S", "0.01")
    monkeypatch.setenv("OFM_AIGEN_POLL_MAX_INTERVAL_S", "0.02")


@pytest.fixture
def slept(monkeypatch) -> list[float]:
    waits: list[float] = []
    monkeypatch.setattr(self_heal, "_sleep", waits.append)
    return waits


@pytest.fixture
def kie() -> Iterator[responses.RequestsMock]:
    with responses.RequestsMock(registry=registries.OrderedRegistry) as rsps:
        yield rsps


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _jobs(path: str) -> str:
    return f"{settings.kieai.jobs_base}/{path}"


def _body(name: str) -> dict:
    return json.loads((FIXTURES / "providers" / "kie" / f"{name}.json").read_text())


def _record_success(kie: responses.RequestsMock) -> None:
    kie.get(_jobs("recordInfo"), json=_body("record_success"))


def _creates(kie: responses.RequestsMock) -> list[dict]:
    return [
        json.loads(c.request.body or b"{}")
        for c in kie.calls
        if c.request.url and "createTask" in c.request.url
    ]


def _signature(task: str, stamp: int, key: str = HMAC_KEY) -> str:
    digest = hmac.new(key.encode(), f"{task}.{stamp}".encode(), hashlib.sha256)
    return base64.b64encode(digest.digest()).decode()


def _callback(
    client: TestClient,
    job_key: str,
    task: str = TASK,
    headers: dict[str, str] | None = None,
    provider: str = "kie",
) -> int:
    """What kie.ai POSTs to callBackUrl when a task ends (docs.kie.ai)."""
    body = {
        "code": 200,
        "msg": "Playground task completed successfully.",
        "data": {
            "taskId": task,
            "model": KIE_SPEC.model_id,
            "state": "success",
            "resultJson": json.dumps({"resultUrls": [RESULT]}),
            "creditsConsumed": 288,
        },
    }
    answer = client.post(
        f"/events/ai/{provider}/{job_key}", json=body, headers=headers or {}
    )
    return answer.status_code


def test_a_create_answered_too_late_goes_on_once_kie_calls_back(
    kie, client, monkeypatch
):
    kie.post(_jobs("createTask"), body=requests.ReadTimeout("read timed out"))
    _record_success(kie)  # the callback's check that the task is ours
    _record_success(kie)  # the run's own poll
    kie.get(RESULT, body=PNG)
    heard: list[int] = []

    def kie_calls_back(_seconds: float) -> None:
        if not heard:
            heard.append(_callback(client, "job-1"))

    monkeypatch.setattr(self_heal, "_sleep", kie_calls_back)

    (path,) = runner.generate(KieProvider("key"), KIE_SPEC, {"prompt": "x"}, "job-1")

    assert path.read_bytes() == PNG
    assert heard == [200]
    [create] = _creates(kie)
    assert create["callBackUrl"] == f"{BASE}events/ai/kie/job-1"
    remembered = tasks.recall("job-1")
    assert remembered is not None
    assert remembered.task_id == TASK
    assert tasks.sent("job-1") is None


def test_no_callback_in_time_fails_the_run_and_a_late_one_still_finds_it(
    kie, client, slept, monkeypatch
):
    """The run gives up; the callback after it remembers the task with the
    job's key, which is what recovery re-runs the failed job from."""
    monkeypatch.setenv("OFM_AIGEN_POLL_TIMEOUT_S", "0")
    kie.post(_jobs("createTask"), body=requests.ReadTimeout("read timed out"))

    with pytest.raises(ProviderUnavailableError, match="not sent again"):
        runner.generate(KieProvider("key"), KIE_SPEC, {"prompt": "x"}, "job-1")
    assert tasks.recall("job-1") is None

    _record_success(kie)
    assert _callback(client, "job-1") == 200

    remembered = tasks.recall("job-1")
    assert remembered is not None
    assert (remembered.task_id, remembered.api_key, remembered.model) == (
        TASK,
        "key",
        KIE_SPEC.key,
    )
    assert slept == []


def test_a_piece_of_a_long_clone_is_found_by_its_own_part(kie, client):
    tasks.remember_sent(
        tasks.ProviderTask(
            job_id="job-1",
            part=2,
            provider="kie",
            model=KIE_SPEC.key,
            task_id="",
            api_key="key",
        )
    )
    _record_success(kie)

    assert _callback(client, "job-1:2") == 200

    assert tasks.recall("job-1") is None
    piece = tasks.recall("job-1", 2)
    assert piece is not None
    assert piece.task_id == TASK
    assert KieProvider("key").callback_url("job-1:2") == f"{BASE}events/ai/kie/job-1:2"


def test_a_callback_for_a_create_that_answered_changes_nothing(kie, client):
    kie.post(_jobs("createTask"), json=_body("create_ok"))
    _record_success(kie)
    kie.get(RESULT, body=PNG)
    runner.generate(KieProvider("key"), KIE_SPEC, {"prompt": "x"}, "job-1")

    assert _callback(client, "job-1", task="task_someone_else") == 200

    remembered = tasks.recall("job-1")
    assert remembered is not None
    assert remembered.task_id == TASK
    assert tasks.sent("job-1") is None


def test_a_callback_for_a_job_that_sent_nothing_is_ignored(kie, client):
    assert _callback(client, "job-9") == 200

    assert tasks.recall("job-9") is None
    assert len(kie.calls) == 0


def test_a_callback_naming_a_task_not_on_the_jobs_key_is_ignored(kie, client):
    tasks.remember_sent(
        tasks.ProviderTask(
            job_id="job-1",
            provider="kie",
            model=KIE_SPEC.key,
            task_id="",
            api_key="key",
        )
    )
    kie.get(_jobs("recordInfo"), json={"code": 422, "msg": "recordInfo is null"})

    assert _callback(client, "job-1", task="task_made_up") == 200

    assert tasks.recall("job-1") is None
    assert tasks.sent("job-1") is not None


def _note(job_id: str = "job-1", part: int = 0, age_s: float = 0) -> None:
    tasks.remember_sent(
        tasks.ProviderTask(
            job_id=job_id,
            part=part,
            provider="kie",
            model=KIE_SPEC.key,
            task_id="",
            api_key="key",
            created_at=time.time() - age_s,
        )
    )


def test_kie_too_slow_to_check_the_callback_still_counts(client):
    """Never answered with an error kie.ai may not retry (its Veo page: three
    tries, then no more callbacks); the run's own poll checks the task."""
    _note()
    with responses.RequestsMock() as busy:  # answers every call
        busy.get(_jobs("recordInfo"), status=503, json={"code": 503, "msg": "busy"})

        assert _callback(client, "job-1") == 200

    remembered = tasks.recall("job-1")
    assert remembered is not None
    assert remembered.task_id == TASK
    assert tasks.sent("job-1") is None


def test_a_rerun_never_sends_a_lost_submit_again_and_takes_its_callback(
    kie, client, monkeypatch
):
    """Recovery re-runs a long clone whose piece lost its answer: that piece
    may be made and paid, so it waits for its callback instead."""
    _note()
    _record_success(kie)  # the callback's check
    _record_success(kie)  # the run's poll
    kie.get(RESULT, body=PNG)
    heard: list[int] = []

    def kie_calls_back(_seconds: float) -> None:
        if not heard:
            heard.append(_callback(client, "job-1"))

    monkeypatch.setattr(self_heal, "_sleep", kie_calls_back)

    (path,) = runner.generate(KieProvider("key"), KIE_SPEC, {"prompt": "x"}, "job-1")

    assert path.read_bytes() == PNG
    assert heard == [200]
    assert _creates(kie) == []


def test_a_rerun_with_no_callback_yet_fails_without_sending_it_again(kie, slept):
    """Sent a generation's wait ago and still not reported: the job fails
    (card "Timed out"), the note stays for a callback that comes later."""
    _note(age_s=settings.aigen.poll_timeout_s + 1)

    with pytest.raises(ProviderUnavailableError, match="not sent again") as caught:
        runner.generate(KieProvider("key"), KIE_SPEC, {"prompt": "x"}, "job-1")

    assert _creates(kie) == []
    assert tasks.sent("job-1") is not None
    assert error_reason(str(caught.value)) == "Timed out"
    assert slept == []


@pytest.mark.parametrize(
    "never_left",
    [
        requests.ConnectTimeout("connect timed out"),
        requests.ConnectionError(
            MaxRetryError(
                None, "/api/v1/jobs/createTask", NewConnectionError(None, "refused")
            )  # type: ignore[arg-type]
        ),
    ],
    ids=["connect-timeout", "refused"],
)
def test_a_create_that_never_left_fails_at_once_and_waits_for_nothing(
    kie, slept, never_left
):
    """No connection made: kie.ai made nothing, nothing can call back."""
    kie.post(_jobs("createTask"), body=never_left)

    with pytest.raises(ProviderUnavailableError, match="unreachable"):
        runner.generate(KieProvider("key"), KIE_SPEC, {"prompt": "x"}, "job-1")

    assert tasks.sent("job-1") is None
    assert slept == []


def test_a_forgotten_job_is_not_brought_back_by_a_late_callback(kie, client):
    """A long clone refused on one piece forgets every piece; a lost piece's
    callback after that must not revive it (recovery would pay again)."""
    _note(part=3)

    tasks.forget("job-1")

    assert _callback(client, "job-1:3") == 200
    assert tasks.recall("job-1", 3) is None
    assert len(kie.calls) == 0


def test_a_signed_callback_is_believed_without_asking_kie(kie, client, monkeypatch):
    monkeypatch.setenv("KIE_AI_WEBHOOK_HMAC_KEY", HMAC_KEY)
    _note()
    now = int(time.time())
    signed = {
        "X-Webhook-Timestamp": str(now),
        "X-Webhook-Signature": _signature(TASK, now),
    }

    assert _callback(client, "job-1", headers=signed) == 200

    remembered = tasks.recall("job-1")
    assert remembered is not None
    assert remembered.task_id == TASK
    assert len(kie.calls) == 0


def test_with_the_key_set_only_a_signed_callback_counts(kie, client, monkeypatch):
    monkeypatch.setenv("KIE_AI_WEBHOOK_HMAC_KEY", HMAC_KEY)
    tasks.remember_sent(
        tasks.ProviderTask(
            job_id="job-1",
            provider="kie",
            model=KIE_SPEC.key,
            task_id="",
            api_key="key",
        )
    )
    now = int(time.time())
    stale = now - 3600
    refused = [
        {},
        {
            "X-Webhook-Timestamp": str(now),
            "X-Webhook-Signature": _signature(TASK, now, "x"),
        },
        {
            "X-Webhook-Timestamp": str(stale),
            "X-Webhook-Signature": _signature(TASK, stale),
        },
    ]

    assert [_callback(client, "job-1", headers=h) for h in refused] == [401] * 3
    assert tasks.recall("job-1") is None

    signed = {
        "X-Webhook-Timestamp": str(now),
        "X-Webhook-Signature": _signature(TASK, now),
    }
    assert _callback(client, "job-1", headers=signed) == 200
    remembered = tasks.recall("job-1")
    assert remembered is not None
    assert remembered.task_id == TASK


def test_an_unknown_provider_calling_in_is_not_found(client):
    assert _callback(client, "job-1", provider="nobody") == 404


def test_a_refused_create_leaves_nothing_waiting(kie):
    kie.post(_jobs("createTask"), json=_body("create_rejected"))

    with pytest.raises(ProviderInputError):
        runner.generate(KieProvider("key"), KIE_SPEC, {"prompt": "x"}, "job-1")

    assert tasks.sent("job-1") is None
    assert tasks.recall("job-1") is None


def test_without_a_public_address_no_callback_is_asked_and_nothing_waits(
    kie, slept, monkeypatch
):
    """Dev (APP_BASE_URL http://localhost): kie.ai could not reach us."""
    monkeypatch.setenv("APP_BASE_URL", "http://localhost:8000/")
    kie.post(_jobs("createTask"), body=requests.ReadTimeout("read timed out"))

    with pytest.raises(ProviderUnavailableError, match="ReadTimeout"):
        runner.generate(KieProvider("key"), KIE_SPEC, {"prompt": "x"}, "job-1")

    [create] = _creates(kie)
    assert "callBackUrl" not in create
    assert tasks.sent("job-1") is None
    assert slept == []


@pytest.mark.usefixtures("fake_externals")
def test_an_elyum_create_whose_answer_is_lost_is_sent_again_and_made_once(slept):
    """Same Idempotency-Key: the resend answers the generation already made."""
    fake_elyum().lose_next = True
    spec = get_spec("elyum:alibaba/wan-3.0/text-to-video")

    (path,) = runner.generate(
        ElyumProvider(FAKE_KEY), spec, {"prompt": "p"}, "elyum-lost"
    )

    assert path.stat().st_size > 0
    assert len(fake_elyum().generations) == 1
    assert [key for _body, key in fake_elyum().created] == ["elyum-lost"]
    assert len(slept) == 1


@pytest.mark.usefixtures("fake_externals")
def test_a_higgsfield_submit_whose_answer_is_lost_is_sent_again_and_made_once(slept):
    """Same Idempotency-Key (docs concepts/idempotency): the resend answers
    the request already made, never a second paid one."""
    fake_higgsfield().lose_next = True
    spec = get_spec("higgsfield:z-image/turbo")

    (path,) = runner.generate(
        HiggsfieldProvider(HF_KEY), spec, {"prompt": "p"}, "hf-lost"
    )

    assert path.stat().st_size > 0
    assert len(fake_higgsfield().submitted) == 1
    assert len(slept) == 1
