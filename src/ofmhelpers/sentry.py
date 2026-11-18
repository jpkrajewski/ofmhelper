"""Crashes and logged errors to Sentry: what Logfire shows as a trace, Sentry
groups into an issue and alerts on.

`configure_sentry(service)` is called once per process by the entrypoints,
next to `configure_telemetry()`: web/main.py, each RQ worker process
(worker.py) and the sweeps process (sweeps.py). The SDK then hooks into
FastAPI (a request that ends in a 5xx), RQ (a failed job) and `logging`
(every `logger.error` / `logger.exception`) by itself.

Nothing is sent without a DSN (`SENTRY_DSN`): tests, CI and a fresh checkout
run with it off and unchanged behaviour.

Errors only. Traces are Logfire's (telemetry.py), so no sample rate is set and
no Sentry header is added to a call going out (`trace_propagation_targets`:
a provider or Instagram would see it).

What keeps secrets out:
- no values of a frame's variables (`include_local_variables`: an API key or a
  cookie is a local somewhere in most tracebacks), no request body, no
  cookies or user (`send_default_pii`);
- `_masked` runs `redact` (redact.py, what Logfire's spans go through) over
  every text of an event before it leaves: messages, exception texts, URLs,
  the request's query string;
- a failed job's arguments are masked by the SDK, and its description (the
  call written out, arguments included) is dropped here.
"""

from typing import Any

import sentry_sdk
lazy from sentry_sdk.types import Event, Hint

from ofmhelpers.config import settings
from ofmhelpers.redact import redact


def configure_sentry(service: str) -> None:
    """Once per process; without a DSN the SDK is left untouched."""
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


def _before_send(event: Event, _hint: Hint) -> Event:
    masked: Event = _masked(event)
    query = masked.get("request", {}).get("query_string")
    if isinstance(query, str):
        # Sent without its "?", which is what tells `redact` a parameter's
        # name from any other word.
        masked["request"]["query_string"] = redact(f"?{query}").removeprefix("?")
    # The SDK masks a failed job's arguments but sends its description, which
    # RQ writes as the call with those same arguments in it.
    job = masked.get("extra", {}).get("rq-job")
    if isinstance(job, dict):
        job.pop("description", None)
    return masked


def _masked(value: Any) -> Any:
    """`value` with every text in it, at any depth, through `redact`."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: _masked(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(_masked(item) for item in value)
    return value
