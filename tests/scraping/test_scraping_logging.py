"""The `except`-block logging contract: the traceback goes in `exc_info`, never
into the message. Interpolating the exception loses the stack that says *where*
it came from, which is the only reason these paths log at all -- they all
swallow the error and return a degraded result."""

import logging
import subprocess

import pytest

from ofmhelpers.scraping import instagram_public


def _records(caplog):
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_subprocess_failure_logs_traceback_not_the_exception_text(monkeypatch, caplog):
    boom = RuntimeError("playwright is on fire")

    def explode(username, last_n_posts):
        raise boom

    monkeypatch.setattr(instagram_public, "_run_and_parse_subprocess", explode)

    with caplog.at_level(logging.WARNING):
        stats = instagram_public.fetch_profile_stats("someone", last_n_posts=1)

    (record,) = _records(caplog)
    assert record.exc_info is not None
    assert str(boom) not in record.getMessage()
    # ... and the caller still gets the message, on the result rather than in
    # the log line.
    assert stats.error == str(boom)


def test_called_process_error_keeps_the_child_stderr_as_its_own_argument(
    monkeypatch, caplog
):
    """The child runs the real scrape, so its stderr is the only place its
    traceback survives -- it stays, as an argument, while the exception text
    does not."""
    exc = subprocess.CalledProcessError(2, "cmd", stderr="child blew up")

    def explode(username, last_n_posts):
        raise exc

    monkeypatch.setattr(instagram_public, "_run_and_parse_subprocess", explode)

    with caplog.at_level(logging.WARNING):
        stats = instagram_public.fetch_profile_stats("someone", last_n_posts=1)

    (record,) = _records(caplog)
    assert record.exc_info is not None
    assert "child blew up" in record.getMessage()
    assert stats.error is not None
    assert "child blew up" in stats.error


@pytest.mark.parametrize(
    "module_name",
    [
        "ofmhelpers.scraping.apify",
        "ofmhelpers.scraping.post_exporter",
        "ofmhelpers.scraping.instagram_stats_job",
        "ofmhelpers.scraping.instagram_public",
        "ofmhelpers.reel_machine.pipeline",
    ],
)
def test_no_module_logs_a_bare_exception_variable(module_name):
    """Guards the whole rule rather than one call site: `, exc)` as the last
    argument of a logger call is the shape this phase removed."""
    import importlib
    import inspect
    import re

    source = inspect.getsource(importlib.import_module(module_name))
    assert not re.search(r"\n\s+exc,\n", source), (
        f"{module_name} passes an exception as a logger argument"
    )
