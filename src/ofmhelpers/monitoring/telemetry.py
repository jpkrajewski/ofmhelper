import contextvars
import gc
import os
import threading
import weakref
from datetime import date
from typing import Any, cast
lazy import logging
lazy from collections.abc import Callable, Mapping

import logfire
from logfire._internal.exporters.otlp import OTLPExporterHttpSession
from logfire._internal.http_transport import install_connection_policy
from opentelemetry.sdk.trace import Event, SpanProcessor
from pydantic import BaseModel, model_serializer
lazy from fastapi import FastAPI, Request, WebSocket
lazy from opentelemetry.context import Context
lazy from opentelemetry.instrumentation.utils import unwrap
lazy from opentelemetry.sdk.trace import ReadableSpan, Span
lazy from sqlalchemy.engine import Engine

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.redact import redact

logger = get_logger(__name__)


class LoggedFields(BaseModel): ...


_configured: list[str] = []

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
        "not_uploaded",
        "va",
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


class SpanAttributes(BaseModel):
    values: dict[str, Any] = {}

    @model_serializer
    def _as_given(self) -> dict[str, Any]:
        return dict(self.values)


def enabled() -> bool:
    return settings.telemetry.token is not None


def configure_telemetry(service: str) -> None:
    if _configured:
        return
    s = settings.telemetry
    logfire.configure(
        token=s.token.get_secret_value() if s.token else None,
        send_to_logfire=s.token is not None,
        service_name=service,
        service_version=s.release,
        environment=settings.infra.environment,
        console=False,
        additional_span_processors=[_Redactor()],
    )
    _renew_connections_at_fork()
    logfire.instrument_sqlalchemy()
    _drop_connect_spans()
    logfire.instrument_requests()
    logfire.instrument_httpx()
    _configured.append(service)


def in_this_trace[T](fn: Callable[..., T]) -> Callable[..., T]:
    context = contextvars.copy_context()

    def call(*args: Any, **kwargs: Any) -> T:
        return context.copy().run(fn, *args, **kwargs)

    return call


def flush_telemetry() -> None:
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
    sessions = weakref.WeakSet(
        o for o in gc.get_objects() if isinstance(o, OTLPExporterHttpSession)
    )
    if sessions:
        os.register_at_fork(after_in_child=lambda: _renew_connections(sessions))


def _renew_connections(sessions: weakref.WeakSet[OTLPExporterHttpSession]) -> None:
    for session in sessions:
        install_connection_policy(session)
        retryer = session.__dict__.pop("retryer", None)
        if retryer is not None:
            retryer.thread = None


def _drop_connect_spans() -> None:
    unwrap(Engine, "connect")


def instrument_app(app: FastAPI) -> None:
    logfire.instrument_fastapi(
        app,
        excluded_urls="/health,/static/.*,/chatting/fanvue/callback",
        request_attributes_mapper=lambda request, attributes: _request_attributes(
            request, attributes
        ).model_dump(),
    )


def _request_attributes(
    _request: Request | WebSocket, attributes: dict[str, Any]
) -> SpanAttributes:
    values = attributes.get("values") or {}
    errors = attributes.get("errors") or []
    return SpanAttributes(
        values={
            **attributes,
            "values": _logged_arguments(values).model_dump(),
            "errors": [
                {key: value for key, value in error.items() if key != "input"}
                for error in errors
            ],
        }
    )


def _logged_arguments(values: Mapping[str, Any]) -> SpanAttributes:
    logged: dict[str, Any] = {}
    for name, value in values.items():
        if isinstance(value, LoggedFields):
            logged.update(_logged_arguments(dict(value)).values)
        elif name in _LOGGED_ARGUMENTS and _is_plain(value):
            logged[name] = value
        else:
            logged[name] = _HIDDEN
    return SpanAttributes(values=logged)


def _is_plain(value: Any) -> bool:
    if isinstance(value, list | tuple):
        return all(_is_plain(item) for item in value)
    return value is None or isinstance(value, str | int | float | date)


def _masked[T](value: T) -> T:
    if isinstance(value, str):
        return cast("T", redact(value))
    if isinstance(value, tuple):
        return cast("T", tuple(_masked(item) for item in value))
    return value


def _masked_attributes(attributes: Mapping[str, Any] | None) -> SpanAttributes:
    return SpanAttributes(
        values={name: _masked(value) for name, value in (attributes or {}).items()}
    )


class _Redactor(SpanProcessor):
    def on_start(
        self,
        span: Span,
        parent_context: Context | None = None,  # noqa: ARG002
    ) -> None:
        attributes = span.attributes or {}
        for name, value in _masked_attributes(attributes).values.items():
            if value != attributes[name]:
                span.set_attribute(name, value)

    def on_end(self, span: ReadableSpan) -> None:
        span._attributes = _masked_attributes(span.attributes).model_dump()  # noqa: SLF001
        span._events = tuple(  # noqa: SLF001
            Event(
                event.name,
                _masked_attributes(event.attributes).model_dump(),
                event.timestamp,
            )
            for event in span.events
        )


def logfire_handler() -> logging.Handler:
    return logfire.LogfireLoggingHandler()
