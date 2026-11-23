# Copyright (C) 2026 jpkrajewski

import json
import logging

import pytest

from ofmhelpers.log import configure_logging, get_logger


@pytest.fixture(autouse=True)
def _restore_logging():
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def test_records_go_to_stdout_not_stderr(monkeypatch, capsys):
    monkeypatch.setenv("OFM_LOG_LEVEL", "INFO")
    monkeypatch.setenv("OFM_LOG_FORMAT", "text")
    configure_logging(force=True)

    get_logger("ofmhelpers.test").info("hello")

    captured = capsys.readouterr()
    assert "hello" in captured.out
    assert "hello" not in captured.err


def test_text_format_includes_level_and_logger_name(monkeypatch, capsys):
    monkeypatch.setenv("OFM_LOG_LEVEL", "INFO")
    monkeypatch.setenv("OFM_LOG_FORMAT", "text")
    configure_logging(force=True)

    get_logger("ofmhelpers.some.module").warning("careful")

    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "ofmhelpers.some.module" in out
    assert "careful" in out


def test_json_format_emits_one_parseable_object_per_record(monkeypatch, capsys):
    monkeypatch.setenv("OFM_LOG_LEVEL", "INFO")
    monkeypatch.setenv("OFM_LOG_FORMAT", "json")
    configure_logging(force=True)

    get_logger("ofmhelpers.test").info("done")

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["level"] == "INFO"
    assert payload["logger"] == "ofmhelpers.test"
    assert payload["msg"] == "done"
    assert "ts" in payload


def test_json_format_keeps_only_its_own_fields_not_a_records_extras(
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("OFM_LOG_FORMAT", "json")
    configure_logging(force=True)

    get_logger("ofmhelpers.test").info("saved", extra={"path": object()})

    payload = json.loads(capsys.readouterr().out.strip())
    assert set(payload) == {"ts", "level", "logger", "msg"}


def test_exc_info_attaches_the_traceback(monkeypatch, capsys):
    monkeypatch.setenv("OFM_LOG_FORMAT", "json")
    configure_logging(force=True)

    try:
        raise ValueError("boom")  # noqa: TRY301, EM101
    except ValueError:
        get_logger("ofmhelpers.test").warning("failed", exc_info=True)

    payload = json.loads(capsys.readouterr().out.strip())
    assert "ValueError: boom" in payload["exc"]


def test_level_is_configurable(monkeypatch, capsys):
    monkeypatch.setenv("OFM_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("OFM_LOG_FORMAT", "text")
    configure_logging(force=True)

    logger = get_logger("ofmhelpers.test")
    logger.info("should be filtered")
    logger.warning("should appear")

    out = capsys.readouterr().out
    assert "should be filtered" not in out
    assert "should appear" in out


def test_repeated_configure_does_not_duplicate_handlers(monkeypatch, capsys):
    monkeypatch.setenv("OFM_LOG_FORMAT", "text")
    configure_logging(force=True)
    configure_logging()
    configure_logging()

    get_logger("ofmhelpers.test").warning("once")

    assert capsys.readouterr().out.count("once") == 1


def test_configure_replaces_handlers_rather_than_appending(monkeypatch, capsys):
    monkeypatch.setenv("OFM_LOG_FORMAT", "text")
    configure_logging(force=True)
    configure_logging(force=True)

    get_logger("ofmhelpers.test").warning("single")

    assert capsys.readouterr().out.count("single") == 1


def test_noisy_third_party_loggers_are_raised_to_warning(monkeypatch, capsys):
    monkeypatch.setenv("OFM_LOG_LEVEL", "INFO")
    monkeypatch.setenv("OFM_LOG_FORMAT", "text")
    configure_logging(force=True)

    logging.getLogger("httpx").info("chatty request line")
    logging.getLogger("urllib3").info("chatty connection line")
    logging.getLogger("httpx2").info("chatty fork line")

    out = capsys.readouterr().out
    assert "chatty request line" not in out
    assert "chatty connection line" not in out
    assert "chatty fork line" not in out


def test_app_loggers_still_emit_at_info_when_third_parties_are_muted(
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("OFM_LOG_LEVEL", "INFO")
    monkeypatch.setenv("OFM_LOG_FORMAT", "text")
    configure_logging(force=True)

    get_logger("ofmhelpers.integrations.ai_providers.kie.client").info(
        "upload starting",
    )

    assert "upload starting" in capsys.readouterr().out


def test_access_log_can_be_disabled(monkeypatch, capsys):
    monkeypatch.setenv("OFM_LOG_ACCESS", "false")
    monkeypatch.setenv("OFM_LOG_FORMAT", "text")
    configure_logging(force=True)

    logging.getLogger("uvicorn.access").info('GET / HTTP/1.1" 200')

    assert "GET /" not in capsys.readouterr().out


def test_uvicorn_error_logger_propagates_into_our_format(monkeypatch, capsys):
    monkeypatch.setenv("OFM_LOG_FORMAT", "json")
    configure_logging(force=True)

    logging.getLogger("uvicorn.error").warning("uvicorn said something")

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["logger"] == "uvicorn.error"
    assert payload["msg"] == "uvicorn said something"
