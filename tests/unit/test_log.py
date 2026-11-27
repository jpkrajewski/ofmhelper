# Copyright (C) 2026 jpkrajewski

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import pytest

from ofmhelpers.acting import Acting, Via, acting
from ofmhelpers.log import configure_logging
from ofmhelpers.monitoring import telemetry
from ofmhelpers.redact import MASK

API_KEY = "sk-" + "a1B2c3D4" * 6


class _SecretValue:
    def __str__(self) -> str:
        return "/files/ai/secret-download-token"


class _Point(NamedTuple):
    x: int
    y: int


@pytest.fixture(autouse=True)
def _restore_logging(monkeypatch):
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def test_stdout_is_one_parseable_json_object_per_record(capsys):
    configure_logging(force=True)

    logging.getLogger("ofmhelpers.test").info("done")

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["level"] == "INFO"
    assert payload["logger"] == "ofmhelpers.test"
    assert payload["msg"] == "done"
    assert payload["context"] == {}
    assert isinstance(payload["process"], int)
    assert payload["thread"]


def test_timestamp_is_iso_8601_in_utc(capsys):
    configure_logging(force=True)

    logging.getLogger("ofmhelpers.test").info("done")

    payload = json.loads(capsys.readouterr().out.strip())
    parsed = datetime.fromisoformat(payload["ts"])
    assert parsed.utcoffset().total_seconds() == 0
    assert payload["ts"].endswith("+00:00")


def test_structured_context_is_preserved_and_secrets_are_masked(capsys):
    configure_logging(force=True)

    logging.getLogger("ofmhelpers.test").info(
        "saved",
        extra={
            "context": {
                "job_id": "job-123",
                "attempt": 2,
                "api_key": API_KEY,
                "nested": {"refresh_token": "keep-me-out"},
            },
        },
    )

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["context"] == {
        "job_id": "job-123",
        "attempt": 2,
        "api_key": MASK,
        "nested": {"refresh_token": MASK},
    }


def test_context_values_are_serialized_without_breaking_logging(capsys):
    configure_logging(force=True)

    logging.getLogger("ofmhelpers.test").info(
        "saved",
        extra={"context": {"path": Path("result.mp4"), "value": object()}},
    )

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["context"]["path"] == "result.mp4"
    assert payload["context"]["value"].startswith("<object object at ")


def test_non_native_context_values_are_redacted_before_serialization(capsys):
    configure_logging(force=True)

    logging.getLogger("ofmhelpers.test").info(
        "saved",
        extra={
            "context": {
                "path": Path("/password/fake-reset-token"),
                "value": _SecretValue(),
            },
        },
    )

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["context"] == {
        "path": f"/password/{MASK}",
        "value": f"/files/ai/{MASK}",
    }


@pytest.mark.parametrize("path", ["/password/fake-reset-token", "/files/ai/secret"])
def test_acting_request_path_is_redacted_after_context_is_added(capsys, path):
    configure_logging(force=True)

    with acting(Acting(via=Via.REQUEST, ref=f"GET {path}")):
        logging.getLogger("ofmhelpers.test").info("request")

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["context"]["ref"] == f"GET {path.rsplit('/', 1)[0]}/{MASK}"


def test_tuple_subclasses_do_not_break_logging(capsys):
    configure_logging(force=True)

    logging.getLogger("ofmhelpers.test").info(
        "point",
        extra={"context": {"point": _Point(1, 2)}},
    )

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["context"]["point"] == [1, 2]


def test_message_and_exception_are_attached_and_redacted(capsys):
    configure_logging(force=True)

    message = f"bad key {API_KEY}"
    try:
        raise ValueError(message)  # noqa: TRY301
    except ValueError:
        logging.getLogger("ofmhelpers.test").warning(
            "failed for https://example.test/?access_token=secret",
            exc_info=True,
        )

    payload = json.loads(capsys.readouterr().out.strip())
    assert "ValueError" in payload["exc"]
    assert API_KEY not in payload["exc"]
    assert MASK in payload["exc"]
    assert payload["msg"] == f"failed for https://example.test/?access_token={MASK}"


def test_repeated_configure_does_not_duplicate_handlers(capsys):
    configure_logging(force=True)
    configure_logging()
    configure_logging()

    logging.getLogger("ofmhelpers.test").warning("once")

    assert capsys.readouterr().out.count("once") == 1


def test_configure_replaces_handlers_rather_than_appending(capsys):
    configure_logging(force=True)
    configure_logging(force=True)

    logging.getLogger("ofmhelpers.test").warning("single")

    assert capsys.readouterr().out.count("single") == 1


def test_noisy_third_party_loggers_are_raised_to_warning(capsys):
    configure_logging(force=True)

    logging.getLogger("httpx").info("chatty request line")
    logging.getLogger("urllib3").info("chatty connection line")
    logging.getLogger("httpx2").info("chatty fork line")

    assert capsys.readouterr().out == ""


def test_uvicorn_logs_propagate_into_json_and_redact_query_secrets(capsys):
    configure_logging(force=True)

    logging.getLogger("uvicorn.access").info(
        'GET /auth/callback?code=secret&next=/ HTTP/1.1" 200',
    )

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["logger"] == "uvicorn.access"
    assert payload["msg"] == f'GET /auth/callback?code={MASK}&next=/ HTTP/1.1" 200'


def test_logfire_handler_is_null_without_a_token():
    assert isinstance(telemetry.logfire_handler(), logging.NullHandler)


def test_logfire_handler_waits_for_telemetry_configuration(monkeypatch):
    monkeypatch.setenv("LOGFIRE_TOKEN", "pylf_v1_eu_test")
    monkeypatch.setattr(telemetry, "_configured", [])

    assert isinstance(telemetry.logfire_handler(), logging.NullHandler)
