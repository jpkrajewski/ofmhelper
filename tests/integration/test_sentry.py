# Copyright (C) 2026 jpkrajewski

import inspect
import logging
import sys
import types
from contextlib import contextmanager
from typing import Any
lazy from collections.abc import Callable, Iterator

import pytest
import sentry_sdk
import sentry_sdk.integrations
import sentry_sdk.scope
import worker_tasks
from fastapi import FastAPI
from fastapi.testclient import TestClient
from rq import Queue, SimpleWorker
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport
from signin import make_user
from starlette.applications import Starlette

from ofmhelpers.background import alive
from ofmhelpers.cache import get_redis
from ofmhelpers.cache.queue import QueueLoad
from ofmhelpers.db.jobs.store import create_job, get_job
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.refusals import RefusedError
from ofmhelpers.integrations.ai_providers.errors import (
    ProviderGenerationError,
    ProviderNoCreditsError,
)
from ofmhelpers.monitoring.sentry import (
    HEARTBEAT_S,
    configure_sentry,
    heartbeat,
    name_user,
)
from ofmhelpers.redact import MASK
from ofmhelpers.web.exceptions import exception_handlers

DSN = "https://public@sentry.invalid/1"
API_KEY = "sk-" + "a1B2c3D4" * 6
PATCHED_PACKAGES = frozenset(
    {
        "starlette",
        "fastapi",
        "rq",
        "redis",
        "logging",
        "threading",
        "subprocess",
        "urllib3",
        "http",
        "sys",
    },
)


class _Kept(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        event = envelope.get_event()
        if event is not None:
            self.events.append(dict(event))
        for item in envelope.items:
            if item.type == "check_in" and item.payload.json is not None:
                self.events.append({"type": "check_in", **item.payload.json})


def _own(owner: Any) -> dict[str, Any]:
    members = dict(inspect.getmembers_static(owner))
    if not isinstance(owner, type):
        return members
    bases = [dict(inspect.getmembers_static(base)) for base in owner.__mro__[1:]]
    return {
        attribute: value
        for attribute, value in members.items()
        if not any(base.get(attribute) is value for base in bases)
    }


def _routines(owner: Any) -> dict[str, Any]:
    return {
        attribute: value
        for attribute, value in _own(owner).items()
        if inspect.isroutine(value) or isinstance(value, classmethod | staticmethod)
    }


type Snapshot = dict[tuple[str, str | None], tuple[Any, dict[str, Any]]]


def _library_functions() -> Snapshot:
    found: Snapshot = {}
    for name, module in list(sys.modules.items()):
        if module is None or name.split(".")[0] not in PATCHED_PACKAGES:
            continue
        owners = {None: module} | {
            key: value
            for key, value in inspect.getmembers_static(module, inspect.isclass)
            if value.__module__.split(".")[0] in PATCHED_PACKAGES
        }
        for key, owner in owners.items():
            found[(name, key)] = (owner, _routines(owner))
    return found


def _defined_by_sentry(function: Any, saved: set[int]) -> bool:
    return id(function) in saved or (
        isinstance(function, types.FunctionType)
        and function.__module__.startswith("sentry_sdk")
    )


def _sentry_made(routine: Any, saved: set[int]) -> bool:
    function = (
        routine.__func__
        if isinstance(routine, classmethod | staticmethod | types.MethodType)
        else routine
    )
    reached = inspect.unwrap(
        function,
        stop=lambda step: _defined_by_sentry(step, saved),
    )
    return _defined_by_sentry(reached, saved)


def _put_back(before: Snapshot) -> None:
    for owner, functions in before.values():
        saved = {id(original) for original in functions.values()}
        for attribute, added in _routines(owner).items():
            if attribute not in functions and _sentry_made(added, saved):
                delattr(owner, attribute)
        current = _routines(owner)
        for attribute, original in functions.items():
            if current.get(attribute) is not original:
                setattr(owner, attribute, original)


@contextmanager
def _sdk_sandbox() -> Iterator[None]:
    before = _library_functions()
    processors = list(sentry_sdk.scope.global_event_processors)
    try:
        yield
    finally:
        sentry_sdk.get_global_scope().set_client(None)
        _put_back(before)
        sentry_sdk.scope.global_event_processors[:] = processors
        sentry_sdk.integrations._installed_integrations.clear()
        sentry_sdk.integrations._processed_integrations.clear()


@pytest.fixture
def sent(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", DSN)
    monkeypatch.setenv("OFM_RELEASE", "abc1234")
    with _sdk_sandbox():
        configure_sentry("ascend-test")
        kept = _Kept()
        sentry_sdk.get_client().transport = kept
        yield kept.events


def _client() -> TestClient:
    app = FastAPI(exception_handlers=exception_handlers().model_dump())

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
    logging.getLogger("ofmhelpers.test_sentry").error("sweep failed for %s", API_KEY)

    assert len(sent) == 1
    assert API_KEY not in str(sent[0])


def test_a_failed_job_is_an_event_without_its_arguments(sent):
    queue = Queue(connection=get_redis())
    job = queue.enqueue(
        worker_tasks.fails_with,
        "argument-value",
        secret="keyword-value",
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
        logging.getLogger("ofmhelpers.test_sentry").error("page failed")

    assert sent[0]["user"] == {"id": "42"}


def test_the_heartbeat_is_a_check_in_that_makes_its_monitor(sent):
    heartbeat()

    [checkin] = sent
    assert checkin["type"] == "check_in"
    assert (checkin["monitor_slug"], checkin["status"]) == ("sweeps", "ok")
    assert checkin["monitor_config"]["schedule"]["value"] == HEARTBEAT_S // 60
    assert checkin["release"] == "abc1234"


def test_an_unhealthy_heartbeat_fails_and_alarms_from_the_second_in_a_row(sent):
    heartbeat(healthy=False)

    [checkin] = sent
    assert checkin["status"] == "error"
    config = checkin["monitor_config"]
    assert (config["failure_issue_threshold"], config["recovery_threshold"]) == (2, 1)


def _loads(**workers: int) -> list[QueueLoad]:
    return [
        QueueLoad(
            name=name,
            workers=count,
            busy=0,
            waiting=0,
            failed=0,
            oldest_wait_s=None,
        )
        for name, count in workers.items()
    ]


def test_every_queue_with_a_worker_is_a_healthy_heartbeat(sent, monkeypatch):
    monkeypatch.setattr(alive, "queue_loads", lambda: _loads(default=10, media=1))

    alive.report_alive()

    [checkin] = sent
    assert (checkin["type"], checkin["status"]) == ("check_in", "ok")


def test_a_queue_nobody_works_on_is_an_error_that_names_it(sent, monkeypatch):
    monkeypatch.setattr(
        alive,
        "queue_loads",
        lambda: _loads(default=10, media=0, edit=0),
    )

    alive.report_alive()

    event, checkin = sent
    assert event["level"] == "error"
    assert event["logentry"]["formatted"] == "no worker takes jobs from: media, edit"
    assert (checkin["type"], checkin["status"]) == ("check_in", "error")


def test_queues_that_cannot_be_read_are_an_error(sent, monkeypatch):
    monkeypatch.setattr(alive, "queue_loads", list)

    alive.report_alive()

    event, checkin = sent
    assert "Redis unreadable" in event["logentry"]["formatted"]
    assert checkin["status"] == "error"


def test_no_dsn_no_heartbeat():
    heartbeat()


def test_configuring_twice_keeps_the_first_client(sent):
    first = sentry_sdk.get_client()

    configure_sentry("ascend-other")

    assert sentry_sdk.get_client() is first
    assert sent == []


def test_the_sdk_leaves_starlette_and_rq_as_it_found_them(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", DSN)
    app_call = Starlette.__call__
    enqueue = Queue.enqueue_job

    with _sdk_sandbox():
        configure_sentry("ascend-test")

        assert Starlette.__call__ is not app_call
        assert Queue.enqueue_job is not enqueue

    assert Starlette.__call__ is app_call
    assert Queue.enqueue_job is enqueue
    assert not sentry_sdk.is_initialized()


def _wrapper_depth(function: Any) -> int:
    depth = 0
    while function.__name__ == "sentry_patched_callhandlers":
        closure = dict(
            zip(function.__code__.co_freevars, function.__closure__, strict=True),
        )
        function = closure["old_callhandlers"].cell_contents
        depth += 1
    return depth


def test_two_inits_leave_one_wrapper_and_the_sandbox_undoes_logging(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", DSN)
    original = logging.Logger.callHandlers
    with _sdk_sandbox():
        configure_sentry("ascend-test")
        configure_sentry("ascend-test")
        assert _wrapper_depth(logging.Logger.callHandlers) == 1
    assert logging.Logger.callHandlers is original
