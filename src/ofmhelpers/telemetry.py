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
  trace of the request or job that wrote it;
- every process forked after it (rq workers, their work horses) renews its
  connections to Logfire, and a horse's last flush is capped.

Nothing is sent without a token (`LOGFIRE_TOKEN`): tests, CI and a fresh
checkout run with telemetry off and unchanged behaviour. Logfire's default
scrubbing drops values of attributes named like secrets (password, token,
api_key, cookie, ...); keys never go into span attributes here.
"""

from __future__ import annotations

import gc
import os
import threading
import weakref
from typing import TYPE_CHECKING

import logfire
from logfire._internal.exporters.otlp import OTLPExporterHttpSession
from logfire._internal.http_transport import install_connection_policy

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

if TYPE_CHECKING:
    import logging

    from fastapi import FastAPI

logger = get_logger(__name__)

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
    _renew_connections_at_fork()
    # Not Redis: the cache's GET/SET per request were ~40% of all records
    # (2026-10-22) and never what a bug was about.
    logfire.instrument_sqlalchemy()
    _drop_connect_spans()
    logfire.instrument_requests()
    logfire.instrument_httpx()
    _configured.append(service)


def flush_telemetry() -> None:
    """Sends what is buffered now: an rq work horse exits with `os._exit`,
    which skips the exporter's own flush at exit (worker.py). At most
    `flush_timeout_s`: the SDK's batch flush ignores its timeout, and a horse
    still flushing holds its worker until RQ's job timeout."""
    if not enabled():
        return
    limit_s = settings.telemetry.flush_timeout_s
    flush = threading.Thread(
        target=logfire.force_flush,
        kwargs={"timeout_millis": int(limit_s * 1000)},
        name="telemetry-flush",
        daemon=True,
    )
    flush.start()
    flush.join(limit_s)
    if flush.is_alive():
        logger.warning("telemetry not sent within %.1f s, going on without it", limit_s)


def _renew_connections_at_fork() -> None:
    """Every forked child gets its own connections to Logfire. A child is a
    copy taken at one instant: a connection-pool lock an export thread held
    then stays held for good (the thread is not copied), so every export and
    the last flush wait forever -- `rq worker-pool` forks its workers and
    each worker a work horse per job, while their export threads run (prod
    2026-10-26: 2 of 10 workers sent nothing for two days, their horses hung
    after every job). Pooled sockets and the retry folder are the parent's
    too. Logfire renews its forwarding sessions at a fork but not this one,
    which `configure` makes and does not expose: found on the heap."""
    sessions = weakref.WeakSet(
        o for o in gc.get_objects() if isinstance(o, OTLPExporterHttpSession)
    )
    if sessions:
        os.register_at_fork(after_in_child=lambda: _renew_connections(sessions))


def _renew_connections(sessions: weakref.WeakSet[OTLPExporterHttpSession]) -> None:
    for session in sessions:
        # Logfire's own adapter, new: its own pool and locks.
        install_connection_policy(session)
        # Made again on the next failed export, with its own lock and folder.
        retryer = session.__dict__.pop("retryer", None)
        if retryer is not None:
            # Unreferenced now, so freed at once: Logfire's exit cleanup
            # never empties the parent's retry folder.
            retryer.thread = None


def _drop_connect_spans() -> None:
    """The SQLAlchemy instrumentation also wraps every pool checkout in a
    "connect" span (~7 per request, 2026-10-22), with no option to turn it
    off: unwrap just that. Each query keeps its span (engines made after
    `instrument_sqlalchemy` are traced at `create_engine`)."""
    from opentelemetry.instrumentation.utils import unwrap
    from sqlalchemy.engine import Engine

    unwrap(Engine, "connect")


def instrument_app(app: FastAPI) -> None:
    """Web requests, each its own trace (web/main.py owns `app`). Health
    checks are left out: one every 30 s is noise."""
    logfire.instrument_fastapi(app, excluded_urls="/health,/static/.*")


def logfire_handler() -> logging.Handler:
    """The `logging` handler that sends records to Logfire (log.py)."""
    return logfire.LogfireLoggingHandler()
