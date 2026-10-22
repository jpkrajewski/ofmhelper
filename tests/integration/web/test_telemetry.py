"""Telemetry (ofmhelpers/telemetry.py): each background job is one span with
the task, job id and who started it, a failure marked as an error with its
exception; each request carries the signed-in user. Without a token
nothing is sent and the logging setup is unchanged."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import logging

import pytest
from fastapi.testclient import TestClient
from logfire.testing import CaptureLogfire
from signin import sign_in

from ofmhelpers import log, telemetry
from ofmhelpers.web.main import app
from ofmhelpers.web.stores.jobs import create_job, get_job, run_job


def _spans(capfire: CaptureLogfire) -> list[dict]:
    return capfire.exporter.exported_spans_as_dict()


def test_a_job_is_one_span_with_its_task_id_and_who(capfire: CaptureLogfire):
    job_id = create_job("download_images", {"urls": ["https://x/1"]}, actor="7")

    run_job(job_id, list, {})

    (span,) = [s for s in _spans(capfire) if s["name"] == "job {task} {job_id}"]
    attrs = span["attributes"]
    assert attrs["job_id"] == job_id
    assert attrs["task"] == "download_images"
    assert attrs["actor"] == "7"
    assert "logfire.level_num" not in attrs  # info, the default: not an error
    assert get_job(job_id).status == "done"


def test_a_failed_job_is_an_error_span_with_its_exception(capfire: CaptureLogfire):
    job_id = create_job("download_images", {}, actor="7")

    def boom() -> None:
        msg = "Wrong API Key"
        raise RuntimeError(msg)

    run_job(job_id, boom, {})

    (span,) = [s for s in _spans(capfire) if s["name"] == "job {task} {job_id}"]
    assert span["attributes"]["logfire.level_num"] >= 17  # error
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


@pytest.mark.parametrize("path", ["/health", "/static/css/app.css"])
def test_health_checks_and_static_files_make_no_spans(capfire: CaptureLogfire, path):
    TestClient(app).get(path)
    assert not [s for s in _spans(capfire) if s["attributes"].get("http.route")]


def test_a_work_horse_flushes_its_spans_before_it_exits(monkeypatch):
    """rq ends a work horse with os._exit (no atexit): each job flushes."""
    from rq.worker import Worker

    from ofmhelpers import worker

    flushed: list[bool] = []
    monkeypatch.setattr(worker, "flush_telemetry", lambda: flushed.append(True))

    def failing(self, job, queue):
        msg = "job blew up"
        raise RuntimeError(msg)

    monkeypatch.setattr(Worker, "perform_job", failing)
    with pytest.raises(RuntimeError):
        worker.ConfiguredWorker.perform_job(
            object.__new__(worker.ConfiguredWorker), None, None
        )
    assert flushed == [True]


@pytest.mark.parametrize(("token", "sends"), [(None, False), ("pylf_v1_eu_test", True)])
def test_only_logfire_token_sends_never_a_credentials_file(monkeypatch, token, sends):
    """A `.logfire/` credentials file in the working directory (the repo,
    mounted into the tests container) must never make tests send spans."""
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
    """Every SQL statement stays visible; the "connect" span the SQLAlchemy
    instrumentation wraps around each checkout (~7 per request) does not."""
    import sqlalchemy
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    from ofmhelpers.config import settings

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
    """session.get_engine builds through the instrumented create_engine even
    though web/db/session.py was imported before telemetry was configured."""
    import sqlalchemy
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    from ofmhelpers.web.db import session

    telemetry.logfire.instrument_sqlalchemy()
    telemetry._drop_connect_spans()
    monkeypatch.setattr(session, "_engine", None)
    try:
        with session.session_scope() as s:
            s.execute(sqlalchemy.text("SELECT 42"))
    finally:
        session.get_engine().dispose()
        monkeypatch.setattr(session, "_engine", None)
        SQLAlchemyInstrumentor().uninstrument()

    names = [s["name"] for s in _spans(capfire)]
    assert any(name.startswith("SELECT") for name in names), names
