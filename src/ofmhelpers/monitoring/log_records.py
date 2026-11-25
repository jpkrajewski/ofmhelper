# Copyright (C) 2026 jpkrajewski

import contextlib
import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any, cast

from opentelemetry import trace
from rq.job import get_current_job

from ofmhelpers.acting import Via, current
from ofmhelpers.redact import MASK, redact

_sensitive_name = re.compile(
    r"(?i)^(?:access_token|api[_-]?key|apikey|auth|authorization|code|credential|key|password|refresh_token|sessionid|sig|signature|state|token)$",
)


class _LogRecord(logging.LogRecord):
    context: dict[str, Any]
    arguments: Any
    kwargs: Any
    description: str
    func: str


def _redacted[T](value: T, *, name: str = "") -> T:
    if name and _sensitive_name.fullmatch(name):
        return cast("T", MASK)
    if isinstance(value, str):
        return cast("T", redact(value))
    if isinstance(value, dict):
        masked = {}
        for key, item in value.items():
            masked[str(key)] = _redacted(item, name=str(key))
        return cast("T", masked)
    if isinstance(value, list):
        return cast("T", list(map(_redacted, value)))
    if isinstance(value, tuple):
        return cast("T", tuple(map(_redacted, value)))
    return (
        value
        if value is None or isinstance(value, bool | int | float)
        else cast("T", redact(str(value)))
    )


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record = cast("_LogRecord", record)
        try:
            context = record.context
        except AttributeError:
            context = {}
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds",
            ),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "service": os.environ.get("OFM_SERVICE", "ofmhelpers"),
            "release": os.environ.get("OFM_RELEASE", "dev"),
            "environment": os.environ.get("OFM_ENV", "development"),
            "process": record.process,
            "thread": record.threadName,
            "context": context,
        }
        with contextlib.suppress(AttributeError):
            payload["arguments"] = record.arguments
        with contextlib.suppress(AttributeError):
            payload["kwargs"] = record.kwargs
        with contextlib.suppress(AttributeError):
            payload["description"] = record.description
        with contextlib.suppress(AttributeError):
            payload["func"] = record.func
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        if record.stack_info:
            payload["stack"] = redact(self.formatStack(record.stack_info))
        return json.dumps(payload, default=str)


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record = cast("_LogRecord", record)
        record.msg = redact(str(record.msg))
        record.args = _redacted(record.args)
        try:
            record.context = _redacted(record.context)
        except AttributeError:
            record.context = {}
        with contextlib.suppress(AttributeError):
            record.arguments = _redacted(record.arguments)
        with contextlib.suppress(AttributeError):
            record.kwargs = _redacted(record.kwargs)
        with contextlib.suppress(AttributeError):
            record.description = redact(str(record.description))
        return True


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record = cast("_LogRecord", record)
        try:
            context = record.context
        except AttributeError:
            context = {}
            record.context = context
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            context["trace_id"] = format(span_context.trace_id, "032x")
            context["span_id"] = format(span_context.span_id, "016x")
        job = get_current_job()
        if job is not None:
            context["rq_job_id"] = job.id
            context["queue"] = job.origin
        acting = current()
        if acting is None:
            return True
        context["via"] = acting.via.value
        context["ref"] = acting.ref
        if acting.actor_id is not None:
            context["actor_id"] = acting.actor_id
        if acting.viewing_id is not None:
            context["viewing_id"] = acting.viewing_id
        if acting.trace_id:
            context["trace_id"] = acting.trace_id
        if acting.via == Via.JOB:
            task, separator, job_id = acting.ref.rpartition(" ")
            if separator:
                context["task"] = task
                context["job_id"] = job_id
        return True
