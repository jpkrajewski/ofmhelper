"""Traces, logs and metrics to Pydantic Logfire (OpenTelemetry).

`configure_telemetry(service)` is called once per process by the
entrypoints only -- web/main.py ("ascend-web") and each RQ worker process
(worker.py, "ascend-worker") -- next to `configure_logging()`:

- `logfire.configure` first (the instrumentors below send nowhere
  without it), tagged with the service, `settings.infra.env` and
  `settings.telemetry.release`;
- then every library the app talks through: SQLAlchemy (each Postgres
  query), `requests` and `httpx` (kie.ai, Higgsfield, Instagram, Google) --
  FastAPI is instrumented by web/main.py, which owns `app`; not Redis
  (the cache's reads are noise);
- the app's own `logging` records go to Logfire too (`log.py` adds
  `logfire_handler()` to the root logger), so a log line lands inside the
  trace of the request or job that wrote it.

Nothing is sent without a token (`LOGFIRE_TOKEN`): tests, CI and a fresh
checkout run with telemetry off and unchanged behaviour. Logfire's default
scrubbing drops values of attributes named like secrets (password, token,
api_key, cookie, ...); keys never go into span attributes here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import logfire

from ofmhelpers.config import settings

if TYPE_CHECKING:
    import logging

    from fastapi import FastAPI

_configured: list[str] = []


def enabled() -> bool:
    return settings.telemetry.token is not None


def configure_telemetry(service: str) -> None:
    """Once per process, before anything it instruments is used."""
    if _configured:
        return
    s = settings.telemetry
    logfire.configure(
        token=s.token.get_secret_value() if s.token else None,
        # Only with LOGFIRE_TOKEN: "if-token-present" also counts a
        # `.logfire/` credentials file in the working directory, which sent
        # the test suite's spans (the tests container mounts the repo).
        send_to_logfire=s.token is not None,
        service_name=service,
        service_version=s.release,
        environment=settings.infra.environment,
        # The app's own log formatter already writes to stdout.
        console=False,
    )
    # Not Redis: the cache's GET/SET per request were ~40% of all records
    # (2026-10-22) and never what a bug was about.
    logfire.instrument_sqlalchemy()
    logfire.instrument_requests()
    logfire.instrument_httpx()
    _configured.append(service)


def flush_telemetry() -> None:
    """Sends what is buffered now: an rq work horse exits with `os._exit`,
    which skips the exporter's own flush at exit (worker.py)."""
    if enabled():
        logfire.force_flush(timeout_millis=5_000)


def instrument_app(app: FastAPI) -> None:
    """Web requests, each its own trace (web/main.py owns `app`). Health
    checks are left out: one every 30 s is noise."""
    logfire.instrument_fastapi(app, excluded_urls="/health,/static/.*")


def logfire_handler() -> logging.Handler:
    """The `logging` handler that sends records to Logfire (log.py)."""
    return logfire.LogfireLoggingHandler()
