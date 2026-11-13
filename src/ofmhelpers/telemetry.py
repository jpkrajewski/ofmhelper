"""Traces, logs and metrics to Pydantic Logfire (OpenTelemetry).

`configure_telemetry(service)` is called once per process by the
entrypoints only -- web/main.py ("ascend-web"), each RQ worker process
(worker.py, "ascend-worker") and the sweeps process (sweeps.py,
"ascend-sweeps") -- next to `configure_logging()`:

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
checkout run with telemetry off and unchanged behaviour.

What keeps secrets out, on top of Logfire's default scrubbing (attributes named
like password, secret, api_key, cookie, ...; not `confirm`, `current`, `token`,
`backup_codes`, and never `http.url`, `url.full`, `url.query` or
`exception.message`, which it treats as safe):
- a request's arguments go through an allow-list (`_request_attributes`): a value
  shows only for a name in `_LOGGED_ARGUMENTS`, and only when it is plain (a
  dependency's result, a whole Job or preset, is no argument whatever it is
  called); arguments taken as one object (`LoggedFields`: a form, a gallery's
  query) go field by field;
- `_Redactor` masks credentials (`redact.py`) in every text attribute of every
  span, including the safe ones and the log messages.
"""

import contextvars
import gc
import os
import threading
import weakref
from datetime import date
from typing import Any
lazy import logging
lazy from collections.abc import Callable, Mapping

import logfire
from logfire._internal.exporters.otlp import OTLPExporterHttpSession
from logfire._internal.http_transport import install_connection_policy
from opentelemetry.sdk.trace import Event, SpanProcessor
lazy from fastapi import FastAPI, Request, WebSocket
lazy from opentelemetry.context import Context
lazy from opentelemetry.sdk.trace import ReadableSpan, Span

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.redact import redact
from ofmhelpers.web.forms import LoggedFields

logger = get_logger(__name__)

_configured: list[str] = []

# Route arguments whose value is useful in a trace and holds no secret or
# personal data. Any other argument shows its name only, so a new form field
# stays out of Logfire until it is added here.
_LOGGED_ARGUMENTS = frozenset(
    {
        "account_id",
        "active",
        "application_id",
        "approved",
        "aspect_ratio",
        "audio",
        "batch",
        "color",
        "copies",
        "date_from",
        "date_to",
        "device",
        "device_id",
        "dl",
        "download",
        "export",
        "file_id",
        "group",
        "hidden",
        "image_manifest",
        "index",
        "job",
        "job_id",
        "keep",
        "kind",
        "limit",
        "link",
        "links",
        "login_id",
        "mirror",
        "mirror_pct",
        "model",
        "offset",
        "output_name",
        "page",
        "path",
        "pictures_manifest",
        "posted",
        "preset",
        "preset_id",
        "preset_name",
        "profile",
        "profiles",
        "prompt",
        "provider",
        "rank",
        "ratio",
        "resolution",
        "role",
        "root",
        "section",
        "selected",
        "size",
        "special",
        "strength",
        "target",
        "trim_secs",
        "upload",
        "upload_id",
        "urls",
        "user_id",
        "video_manifest",
        "videos_manifest",
    }
)
_HIDDEN = "[hidden]"


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
        # Runs before Logfire's exporter, which comes later in its pipeline.
        additional_span_processors=[_Redactor()],
    )
    _renew_connections_at_fork()
    # Not Redis: the cache's GET/SET per request were ~40% of all records
    # (2026-10-22) and never what a bug was about.
    logfire.instrument_sqlalchemy()
    _drop_connect_spans()
    logfire.instrument_requests()
    logfire.instrument_httpx()
    _configured.append(service)


def in_this_trace[T](fn: Callable[..., T]) -> Callable[..., T]:
    """`fn` for a pool thread, in the calling thread's trace: a new thread
    starts with an empty context, so its spans would each be a trace of their
    own (and log lines of nothing)."""
    context = contextvars.copy_context()

    def call(*args: Any, **kwargs: Any) -> T:
        return context.copy().run(fn, *args, **kwargs)

    return call


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
    logfire.instrument_fastapi(
        app,
        # The Fanvue callback's URL holds its one-time login code.
        excluded_urls="/health,/static/.*,/chatting/fanvue/callback",
        request_attributes_mapper=_request_attributes,
    )


def _request_attributes(
    _request: Request | WebSocket, attributes: dict[str, Any]
) -> dict[str, Any]:
    """A route's arguments as they go to Logfire: values only for allowed names,
    and no `input` on a validation error (it repeats what was submitted)."""
    values = attributes.get("values") or {}
    errors = attributes.get("errors") or []
    return {
        **attributes,
        "values": _logged_arguments(values),
        "errors": [
            {key: value for key, value in error.items() if key != "input"}
            for error in errors
        ],
    }


def _logged_arguments(values: Mapping[str, Any]) -> dict[str, Any]:
    logged: dict[str, Any] = {}
    for name, value in values.items():
        if isinstance(value, LoggedFields):
            logged.update(_logged_arguments(dict(value)))
        elif name in _LOGGED_ARGUMENTS and _is_plain(value):
            logged[name] = value
        else:
            logged[name] = _HIDDEN
    return logged


def _is_plain(value: Any) -> bool:
    if isinstance(value, list | tuple):
        return all(_is_plain(item) for item in value)
    return value is None or isinstance(value, str | int | float | date)


def _masked(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, tuple):
        return tuple(_masked(item) for item in value)
    return value


def _masked_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    return {name: _masked(value) for name, value in (attributes or {}).items()}


class _Redactor(SpanProcessor):
    """Masks credentials in a span's text before it is exported. At the start
    too: Logfire sends a placeholder of a long span with what it had by then,
    through its exporter only."""

    def on_start(
        self,
        span: Span,
        parent_context: Context | None = None,  # noqa: ARG002 -- SpanProcessor's signature
    ) -> None:
        attributes = span.attributes or {}
        for name, value in _masked_attributes(attributes).items():
            if value != attributes[name]:
                span.set_attribute(name, value)

    def on_end(self, span: ReadableSpan) -> None:
        # OpenTelemetry has no public way to change an ended span, and the next
        # processor in line (the exporter) reads this same object.
        span._attributes = _masked_attributes(span.attributes)  # noqa: SLF001
        span._events = tuple(  # noqa: SLF001
            Event(event.name, _masked_attributes(event.attributes), event.timestamp)
            for event in span.events
        )


def logfire_handler() -> logging.Handler:
    """The `logging` handler that sends records to Logfire (log.py)."""
    return logfire.LogfireLoggingHandler()
