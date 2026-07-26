"""
Covers ofmhelpers/log.py: the app-wide logging setup every entrypoint calls.

The behaviors worth pinning are the ones that break silently in production --
records going nowhere, the wrong stream, a format nothing can parse, or a
duplicate handler quietly doubling every line.
"""

import json
import logging

import pytest

from ofmhelpers.log import configure_logging, get_logger


@pytest.fixture(autouse=True)
def _restore_logging():
    """configure_logging() mutates process-global logging state, so snapshot
    the root handlers/level and put them back after each test -- otherwise a
    test that switches to JSON leaks that format into every later test's
    captured output."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def test_records_go_to_stdout_not_stderr(monkeypatch, capsys):
    """Docker captures both, but the app deliberately uses one stream so
    lines can't interleave out of order between two buffers."""
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


def test_json_format_promotes_extra_fields_to_top_level(monkeypatch, capsys):
    """`extra={...}` is what makes a log line queryable by job id rather than
    something you have to regex out of a message string."""
    monkeypatch.setenv("OFM_LOG_FORMAT", "json")
    configure_logging(force=True)

    get_logger("ofmhelpers.test").info("job finished", extra={"job_id": "abc123"})

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["job_id"] == "abc123"


def test_json_format_survives_a_non_serializable_extra_value(monkeypatch, capsys):
    """A stray Path/datetime in `extra` must degrade to its string form, not
    raise inside the logging call and lose the record entirely."""
    monkeypatch.setenv("OFM_LOG_FORMAT", "json")
    configure_logging(force=True)

    get_logger("ofmhelpers.test").info("saved", extra={"path": object()})

    payload = json.loads(capsys.readouterr().out.strip())
    assert isinstance(payload["path"], str)


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
    """The real failure this guards against: two entrypoints both configuring
    and every line getting logged twice."""
    monkeypatch.setenv("OFM_LOG_FORMAT", "text")
    configure_logging(force=True)
    configure_logging()  # idempotent -- must be a no-op
    configure_logging()

    get_logger("ofmhelpers.test").warning("once")

    assert capsys.readouterr().out.count("once") == 1


def test_configure_replaces_handlers_rather_than_appending(monkeypatch, capsys):
    """force=True reconfigures; it must not leave the previous handler
    attached alongside the new one."""
    monkeypatch.setenv("OFM_LOG_FORMAT", "text")
    configure_logging(force=True)
    configure_logging(force=True)

    get_logger("ofmhelpers.test").warning("single")

    assert capsys.readouterr().out.count("single") == 1


def test_noisy_third_party_loggers_are_raised_to_warning(monkeypatch, capsys):
    """httpx/urllib3 at INFO log a line per request, which would bury the
    app's own output in the container log."""
    monkeypatch.setenv("OFM_LOG_LEVEL", "INFO")
    monkeypatch.setenv("OFM_LOG_FORMAT", "text")
    configure_logging(force=True)

    logging.getLogger("httpx").info("chatty request line")
    logging.getLogger("urllib3").info("chatty connection line")
    # httpx2, not httpx, is the fork this project depends on -- easy to miss.
    logging.getLogger("httpx2").info("chatty fork line")

    out = capsys.readouterr().out
    assert "chatty request line" not in out
    assert "chatty connection line" not in out
    assert "chatty fork line" not in out


def test_app_loggers_still_emit_at_info_when_third_parties_are_muted(
    monkeypatch, capsys
):
    """Guards the obvious way to implement the test above wrong: muting the
    noise by raising the root level, which would silence the app too."""
    monkeypatch.setenv("OFM_LOG_LEVEL", "INFO")
    monkeypatch.setenv("OFM_LOG_FORMAT", "text")
    configure_logging(force=True)

    get_logger("ofmhelpers.aigenproviders.kaiai.client").info("upload starting")

    assert "upload starting" in capsys.readouterr().out


def test_access_log_can_be_disabled(monkeypatch, capsys):
    monkeypatch.setenv("OFM_LOG_ACCESS", "false")
    monkeypatch.setenv("OFM_LOG_FORMAT", "text")
    configure_logging(force=True)

    logging.getLogger("uvicorn.access").info('GET / HTTP/1.1" 200')

    assert "GET /" not in capsys.readouterr().out


def test_uvicorn_error_logger_propagates_into_our_format(monkeypatch, capsys):
    """Uvicorn's startup/shutdown messages should come out in the app's
    format, not uvicorn's own."""
    monkeypatch.setenv("OFM_LOG_FORMAT", "json")
    configure_logging(force=True)

    logging.getLogger("uvicorn.error").warning("uvicorn said something")

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["logger"] == "uvicorn.error"
    assert payload["msg"] == "uvicorn said something"
