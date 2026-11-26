# Copyright (C) 2026 jpkrajewski

from contextlib import contextmanager
from typing import cast
lazy from collections.abc import Generator

import sentry_sdk
import sentry_sdk.crons
import sentry_sdk.crons.consts
lazy from sentry_sdk.types import Event, Hint

from ofmhelpers.config import settings
from ofmhelpers.redact import redact


def configure_sentry(service: str) -> None:
    dsn = settings.sentry.dsn
    if dsn is None or sentry_sdk.is_initialized():
        return
    sentry_sdk.init(
        dsn=dsn.get_secret_value(),
        environment=settings.infra.environment,
        release=settings.telemetry.release,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        trace_propagation_targets=[],
        before_send=_before_send,
    )
    sentry_sdk.set_tag("service", service)


def name_user(user_id: int | str | None) -> None:
    if user_id is not None:
        sentry_sdk.get_isolation_scope().set_user({"id": str(user_id)})


@contextmanager
def user_scope(user_id: str | None) -> Generator[None]:
    with sentry_sdk.isolation_scope():
        name_user(user_id)
        yield


HEARTBEAT_S = 300
_MONITOR = "sweeps"


def heartbeat(*, healthy: bool = True) -> None:
    if not sentry_sdk.is_initialized():
        return
    status = sentry_sdk.crons.consts.MonitorStatus
    sentry_sdk.crons.capture_checkin(
        monitor_slug=_MONITOR,
        status=status.OK if healthy else status.ERROR,
        monitor_config={
            "schedule": {
                "type": "interval",
                "value": HEARTBEAT_S // 60,
                "unit": "minute",
            },
            "checkin_margin": 10,
            "max_runtime": 5,
            "failure_issue_threshold": 2,
            "recovery_threshold": 1,
        },
    )


def _before_send(event: Event, _hint: Hint) -> Event:
    masked = _masked(event)
    query = masked.get("request", {}).get("query_string")
    if isinstance(query, str):
        masked["request"]["query_string"] = redact(f"?{query}").removeprefix("?")
    job = masked.get("extra", {}).get("rq-job")
    if isinstance(job, dict):
        job.pop("description", None)
    crumbs = masked.get("breadcrumbs", {})
    if isinstance(crumbs, dict) and isinstance(crumbs.get("values"), list):
        crumbs["values"] = [
            c
            for c in crumbs["values"]
            if not (isinstance(c, dict) and c.get("category") == "redis")
        ]
    return masked


def _masked[T](value: T) -> T:
    if isinstance(value, str):
        return cast("T", redact(value))
    if isinstance(value, dict):
        return cast("T", {key: _masked(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return cast("T", type(value)(_masked(item) for item in value))
    return value
