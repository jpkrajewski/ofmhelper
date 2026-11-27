# Copyright (C) 2026 jpkrajewski

import json
import logging

import pytest
import sqlalchemy
from fastapi.testclient import TestClient
from logfire.testing import CaptureLogfire
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from rq.worker import Worker
from signin import make_user, sign_in

from ofmhelpers import log
from ofmhelpers.background import worker
from ofmhelpers.cache import QueueJob, QueueName
from ofmhelpers.config import settings
from ofmhelpers.db import session
from ofmhelpers.db.jobs.store import create_job, get_job
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.monitoring import telemetry
from ofmhelpers.web.main import app


def _spans(capfire: CaptureLogfire) -> list[dict]:
    return capfire.exporter.exported_spans_as_dict()


def test_a_job_is_one_span_with_its_task_id_and_who(capfire: CaptureLogfire):
    who = str(make_user().id)
    job_id = create_job("download_images", {"urls": ["https://x/1"]}, actor=who)

    run_job(job_id, list, {})

    (span,) = [s for s in _spans(capfire) if s["name"] == "job {task} {job_id}"]
    attrs = span["attributes"]
    assert attrs["job_id"] == job_id
    assert attrs["task"] == "download_images"
    assert attrs["actor"] == who
    assert "logfire.level_num" not in attrs
    assert get_job(job_id).status == "done"


def test_job_logs_carry_job_task_actor_and_trace(capsys):
    log.configure_logging(force=True)
    who = str(make_user().id)
    job_id = create_job("download_images", {}, actor=who)

    def write_log() -> None:
        logging.getLogger("ofmhelpers.test.job").info("inside job")

    run_job(job_id, write_log, {})

    payloads = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    record = next(payload for payload in payloads if payload["msg"] == "inside job")
    assert record["context"]["job_id"] == job_id
    assert record["context"]["task"] == "download_images"
    assert record["context"]["actor_id"] == int(who)
    assert record["context"]["via"] == "job"
    assert len(record["context"]["trace_id"]) == 32
    assert len(record["context"]["span_id"]) == 16


def test_a_failed_job_is_an_error_span_with_its_exception(capfire: CaptureLogfire):
    job_id = create_job("download_images", {}, actor=str(make_user().id))

    def boom() -> None:
        msg = "Wrong API Key"
        raise RuntimeError(msg)

    run_job(job_id, boom, {})

    (span,) = [s for s in _spans(capfire) if s["name"] == "job {task} {job_id}"]
    assert span["attributes"]["logfire.level_num"] >= 17
    (event,) = [e for e in span["events"] if e["name"] == "exception"]
    assert event["attributes"]["exception.message"] == "Wrong API Key"
    assert get_job(job_id).error == "Wrong API Key"


def test_a_request_carries_the_signed_in_user(capfire: CaptureLogfire):
    client = TestClient(app)
    user = sign_in(client)

    assert client.get("/generate").status_code == 200

    server = [
        s
        for s in _spans(capfire)
        if s["attributes"].get("http.route") == "/generate"
        and s["attributes"].get("enduser.email")
    ]
    assert server, "no request span with the user on it"
    assert server[0]["attributes"]["enduser.id"] == str(user.id)
    assert server[0]["attributes"]["enduser.email"] == user.email


def test_without_a_token_nothing_is_sent_and_logging_is_as_before(monkeypatch):
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    assert not telemetry.enabled()
    log.configure_logging(force=True)
    kinds = {type(h).__name__ for h in logging.getLogger().handlers}
    assert "LogfireLoggingHandler" not in kinds


def test_with_a_token_log_records_go_to_logfire_too(monkeypatch):
    monkeypatch.setenv("LOGFIRE_TOKEN", "pylf_v1_eu_test")
    try:
        log.configure_logging(force=True)
        kinds = {type(h).__name__ for h in logging.getLogger().handlers}
        assert "LogfireLoggingHandler" in kinds
    finally:
        monkeypatch.delenv("LOGFIRE_TOKEN")
        log.configure_logging(force=True)


def test_structured_context_reaches_logfire(capfire: CaptureLogfire, monkeypatch):
    monkeypatch.setenv("LOGFIRE_TOKEN", "pylf_v1_eu_test")
    try:
        log.configure_logging(force=True)
        logging.getLogger("ofmhelpers.test").info(
            "structured event",
            extra={"context": {"job_id": "job-123", "attempt": 2}},
        )

        (span,) = [s for s in _spans(capfire) if s["name"] == "structured event"]
        assert json.loads(span["attributes"]["context"]) == {
            "job_id": "job-123",
            "attempt": 2,
        }
    finally:
        monkeypatch.delenv("LOGFIRE_TOKEN")
        log.configure_logging(force=True)


@pytest.mark.parametrize("path", ["/health", "/static/css/pages.css"])
def test_health_checks_and_static_files_make_no_spans(capfire: CaptureLogfire, path):
    TestClient(app).get(path)
    assert not [s for s in _spans(capfire) if s["attributes"].get("http.route")]


def test_a_work_horse_flushes_its_spans_before_it_exits(monkeypatch):
    flushed: list[bool] = []
    monkeypatch.setattr(worker, "flush_telemetry", lambda: flushed.append(True))
    taken = QueueJob(
        rq_job_id="r",
        func="f",
        queue=QueueName.MEDIA,
        waited_s=0,
        waiting=0,
    )
    monkeypatch.setattr(QueueJob, "taken", lambda job, queue: taken)

    def failing(self, job, queue, execution):
        msg = "job blew up"
        raise RuntimeError(msg)

    monkeypatch.setattr(Worker, "perform_job", failing)
    with pytest.raises(RuntimeError):
        worker.ConfiguredWorker.perform_job(
            object.__new__(worker.ConfiguredWorker),
            None,
            None,
            None,
        )
    assert flushed == [True]


@pytest.mark.parametrize(("token", "sends"), [(None, False), ("pylf_v1_eu_test", True)])
def test_only_logfire_token_sends_never_a_credentials_file(monkeypatch, token, sends):
    if token is None:
        monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    else:
        monkeypatch.setenv("LOGFIRE_TOKEN", token)
    seen: dict = {}
    monkeypatch.setattr(telemetry.logfire, "configure", lambda **kw: seen.update(kw))
    for name in ("instrument_sqlalchemy", "instrument_requests", "instrument_httpx"):
        monkeypatch.setattr(telemetry.logfire, name, lambda: None)
    monkeypatch.setattr(telemetry, "_configured", [])

    telemetry.configure_telemetry("ascend-test")

    assert seen["send_to_logfire"] is sends


def test_a_blank_token_is_off(monkeypatch):
    monkeypatch.setenv("LOGFIRE_TOKEN", "  ")
    assert not telemetry.enabled()


def test_a_query_is_a_span_but_a_pool_checkout_is_not(capfire: CaptureLogfire):
    telemetry.logfire.instrument_sqlalchemy()
    telemetry._drop_connect_spans()
    try:
        engine = sqlalchemy.create_engine(settings.infra.database_url)
        with engine.connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        engine.dispose()
    finally:
        SQLAlchemyInstrumentor().uninstrument()

    names = [s["name"] for s in _spans(capfire)]
    assert any(name.startswith("SELECT") for name in names), names
    assert "connect" not in names


def test_the_apps_own_engine_traces_its_queries(capfire: CaptureLogfire, monkeypatch):
    telemetry.logfire.instrument_sqlalchemy()
    telemetry._drop_connect_spans()
    url = sqlalchemy.make_url(settings.infra.database_url).update_query_dict(
        {"application_name": "telemetry-test"},
    )
    monkeypatch.setenv("OFM_DATABASE_URL", url.render_as_string(hide_password=False))
    try:
        with session.session_scope() as s:
            s.execute(sqlalchemy.text("SELECT 42"))
    finally:
        session.get_engine().dispose()
        SQLAlchemyInstrumentor().uninstrument()

    names = [s["name"] for s in _spans(capfire)]
    assert any(name.startswith("SELECT") for name in names), names
