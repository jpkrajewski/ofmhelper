from collections.abc import Callable
from typing import Any

import pytest
import sentry_sdk
import worker_tasks
from fastapi import FastAPI
from fastapi.testclient import TestClient
from rq import Queue, SimpleWorker
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport
from signin import make_user

from ofmhelpers.cache import get_redis
from ofmhelpers.db.stores.jobs import create_job, get_job
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.refusals import RefusedError
from ofmhelpers.integrations.ai_providers.errors import (
    ProviderGenerationError,
    ProviderNoCreditsError,
)
from ofmhelpers.log import get_logger
from ofmhelpers.redact import MASK
from ofmhelpers.sentry import HEARTBEAT_S, configure_sentry, heartbeat, name_user
from ofmhelpers.web.exceptions import exception_handlers

DSN = "https://public@sentry.invalid/1"
API_KEY = "sk-" + "a1B2c3D4" * 6


class _Kept(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        event = envelope.get_event()
        if event is not None:
            self.events.append(event)
        for item in envelope.items:
            if item.type == "check_in" and item.payload.json is not None:
                self.events.append({"type": "check_in", **item.payload.json})


@pytest.fixture
def sent(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", DSN)
    monkeypatch.setenv("OFM_RELEASE", "abc1234")
    configure_sentry("ascend-test")
    kept = _Kept()
    sentry_sdk.get_client().transport = kept
    yield kept.events
    sentry_sdk.get_global_scope().set_client(None)


def _client() -> TestClient:
    app = FastAPI(exception_handlers=exception_handlers())

    @app.post("/crash")
    def crash(secret: str = "") -> None:
        api_key = API_KEY
        msg = f"provider refused key {api_key} {secret}"
        raise RuntimeError(msg)

    @app.get("/refusal")
    def refusal() -> None:
        msg = "Pick a video first."
        raise RefusedError(msg)

    return TestClient(app, raise_server_exceptions=False)


def test_no_dsn_leaves_the_sdk_alone():
    configure_sentry("ascend-test")

    assert not sentry_sdk.is_initialized()


def test_a_crash_is_one_event_that_says_where_it_ran(sent):
    response = _client().post("/crash")

    assert response.status_code == 500
    assert len(sent) == 1
    event = sent[0]
    assert event["environment"] == "development"
    assert event["release"] == "abc1234"
    assert event["tags"]["service"] == "ascend-test"
    assert event["exception"]["values"][-1]["type"] == "RuntimeError"


def test_a_refusal_is_no_event(sent):
    response = _client().get("/refusal")

    assert response.status_code == 400
    assert sent == []


def test_a_key_in_the_error_text_is_masked(sent):
    _client().post("/crash")

    assert API_KEY not in str(sent[0])
    assert MASK in sent[0]["exception"]["values"][-1]["value"]


def test_an_event_holds_no_variables_body_or_cookies(sent):
    client = _client()
    client.cookies.set("ofm_session", "session-cookie-value")

    client.post("/crash?token=query-token-value", json={"secret": "body-secret-value"})

    event = sent[0]
    frames = event["exception"]["values"][-1]["stacktrace"]["frames"]
    assert all("vars" not in frame for frame in frames)
    assert not event["request"].get("data")
    whole = str(event)
    assert "body-secret-value" not in whole
    assert "session-cookie-value" not in whole
    assert "query-token-value" not in whole


def test_a_logged_error_is_an_event(sent):
    get_logger("ofmhelpers.test_sentry").error("sweep failed for %s", API_KEY)

    assert len(sent) == 1
    assert API_KEY not in str(sent[0])


def test_a_failed_job_is_an_event_without_its_arguments(sent):
    queue = Queue(connection=get_redis())
    job = queue.enqueue(
        worker_tasks.fails_with, "argument-value", secret="keyword-value"
    )

    SimpleWorker([queue], connection=get_redis()).work(burst=True)

    assert job.is_failed
    assert len(sent) == 1
    whole = str(sent[0])
    assert "argument-value" not in whole
    assert "keyword-value" not in whole
    assert sent[0]["extra"]["rq-job"]["job_id"] == job.id
    assert "description" not in sent[0]["extra"]["rq-job"]


def _failing(exc: Exception) -> Callable[[], None]:
    def run() -> None:
        raise exc

    return run


def test_a_job_a_provider_refused_is_no_event(sent):
    refusals = [
        ProviderNoCreditsError("kie.ai: Credits insufficient", provider="kie.ai"),
        ProviderGenerationError("APIYI: [DataInspectionFailed] Green net check"),
        RefusedError("Pick a video first."),
    ]
    for exc in refusals:
        job_id = create_job("ai", {"prompt": "p"}, actor=str(make_user().id))

        run_job(job_id, _failing(exc), {})

        job = get_job(job_id)
        assert job is not None
        assert job.status == "failed"
    assert sent == []


def test_a_job_that_crashed_is_one_event(sent):
    job_id = create_job("ai", {"prompt": "p"}, actor=str(make_user().id))

    run_job(job_id, _failing(RuntimeError("list index out of range")), {})

    assert len(sent) == 1
    assert sent[0]["exception"]["values"][-1]["type"] == "RuntimeError"


def test_a_jobs_event_names_its_person_by_id_only(sent):
    person = make_user("crashed@example.com")
    job_id = create_job("ai", {"prompt": "p"}, actor=str(person.id))

    run_job(job_id, _failing(RuntimeError("boom")), {})

    assert sent[0]["user"] == {"id": str(person.id)}
    assert "crashed@example.com" not in str(sent[0])
    crumbs = sent[0].get("breadcrumbs", {}).get("values", [])
    assert not [c for c in crumbs if c.get("category") == "redis"]


def test_a_named_request_names_its_person_by_id_only(sent):
    with sentry_sdk.isolation_scope():
        name_user(42)
        get_logger("ofmhelpers.test_sentry").error("page failed")

    assert sent[0]["user"] == {"id": "42"}


def test_the_heartbeat_is_a_check_in_that_makes_its_monitor(sent):
    heartbeat()

    [checkin] = sent
    assert checkin["type"] == "check_in"
    assert (checkin["monitor_slug"], checkin["status"]) == ("sweeps", "ok")
    assert checkin["monitor_config"]["schedule"]["value"] == HEARTBEAT_S // 60
    assert checkin["release"] == "abc1234"


def test_no_dsn_no_heartbeat():
    heartbeat()


def test_configuring_twice_keeps_the_first_client(sent):
    first = sentry_sdk.get_client()

    configure_sentry("ascend-other")

    assert sentry_sdk.get_client() is first
    assert sent == []
