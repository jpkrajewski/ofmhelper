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
