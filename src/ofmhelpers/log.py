"""
Logging setup for the whole app. Two things live here and nothing else:

  configure_logging()  -- called ONCE per process, by an entrypoint
  get_logger(__name__) -- called by every module that wants to log

Named `log.py`, not `logging.py`, so it can never be confused with (or
shadow) the stdlib module in a reader's head.

Why a module instead of `logging.basicConfig()` at the top of each file:
this app runs as two long-lived containers (uvicorn API + `rq worker-pool`)
plus a couple of one-shot scripts, and each of those needs identical output
on stdout for `docker compose logs` to be useful. Configuring in one place
also lets us fold uvicorn's own three loggers into the same format instead
of interleaving two different ones.

Conventions for call sites:
  - `logger = get_logger(__name__)` at module scope. Never configure, never
    add handlers, never set a level in a library module -- that's the
    entrypoint's job, and doing it at import time makes the setting depend
    on import order.
  - Use lazy `%s` args (`logger.info("got %s", x)`), not f-strings, so the
    formatting cost is skipped when the level is disabled and so log
    aggregators can group by the message template.
  - `logger.exception(...)` inside an `except` block: it attaches the
    traceback automatically. Don't pass the exception in yourself.

Output goes to **stdout**, not stderr: in Docker both are captured, but
keeping one stream means lines can't interleave out of order the way two
independently-buffered streams can. `PYTHONUNBUFFERED=1` (set in the
Dockerfile) is what makes them appear immediately.

Who calls configure_logging():
  - `web/main.py` (uvicorn imports it to find `app`)
  - `worker.py` -- twice, deliberately: once in the pool parent, and again
    inside each worker child via ConfiguredWorker.work(), because
    `rq worker-pool` forks children that never ran the parent's setup.
Deliberately NOT alembic (`alembic/env.py` does its own `fileConfig()` from
alembic.ini -- that's alembic's convention and it's a one-shot deploy step)
or `web/db/backfill_remote_urls.py` (an operator CLI whose stdout is its
output; see its module docstring).
"""

from __future__ import annotations

import json
import logging
import logging.config
import sys
from typing import Any

# Set by configure_logging() so a second call (e.g. an entrypoint that
# imports another entrypoint) doesn't tear down and rebuild handlers.
_configured = False

# Attributes present on every LogRecord. Anything NOT in here was passed by
# the caller via `extra={...}`, and is what the JSON formatter promotes to
# top-level fields.
_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | frozenset({"asctime", "message", "taskName"})


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Anything passed as `extra={...}` at the call
    site becomes a top-level key, so `logger.info("done", extra={"job_id": x})`
    is queryable in a log aggregator rather than buried in a text blob."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        # default=str so a stray Path/datetime in `extra` degrades to its
        # string form instead of raising inside the logging call.
        return json.dumps(payload, default=str)


def _config(level: str, fmt: str, access_log: bool) -> dict[str, Any]:
    formatters: dict[str, Any] = {
        "text": {
            # Fixed-width level keeps the message column aligned when you're
            # reading a wall of `docker compose logs`.
            "format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "json": {"()": JsonFormatter},
    }

    return {
        "version": 1,
        # Loggers already created at import time (module-scope get_logger
        # calls, which is all of them) must keep working -- disabling them
        # here is the classic way to silence half an app by accident.
        "disable_existing_loggers": False,
        "formatters": formatters,
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "stream": sys.stdout,
                "formatter": "json" if fmt == "json" else "text",
            }
        },
        "root": {"handlers": ["stdout"], "level": level},
        "loggers": {
            # Uvicorn installs its own handlers on these three. Clearing
            # them and propagating to root is what makes API logs and app
            # logs come out in one consistent format.
            "uvicorn": {"handlers": [], "propagate": True},
            "uvicorn.error": {"handlers": [], "propagate": True},
            "uvicorn.access": {
                "handlers": [],
                "propagate": access_log,
                "level": "INFO" if access_log else "CRITICAL",
            },
            # RQ checks for an "effective handler" before installing its own
            # colorizing stdout/stderr pair (rq.logutils.setup_loghandlers).
            # Because configure_logging() puts a handler on root first, that
            # check passes and RQ leaves ours alone -- job logs come out in
            # this format, on one stream. Listed explicitly so the coupling
            # is visible if RQ ever changes that behavior.
            "rq": {"level": "INFO", "propagate": True},
            # Chatty third-party libs: their INFO is our DEBUG. Raise them
            # explicitly rather than raising the root level and going blind.
            # httpx2 is the fork this project actually depends on (see the
            # dev group in pyproject.toml); it logs a line per request.
            "httpx": {"level": "WARNING"},
            "httpx2": {"level": "WARNING"},
            "httpcore": {"level": "WARNING"},
            "urllib3": {"level": "WARNING"},
            "requests": {"level": "WARNING"},
            "apscheduler": {"level": "WARNING"},
            "openpyxl": {"level": "WARNING"},
        },
    }


def configure_logging(*, force: bool = False) -> None:
    """Install the app-wide logging config. Idempotent: safe to call from
    several entrypoints, only the first one takes effect (pass force=True to
    reconfigure, which only tests should need).

    Call this as the FIRST thing an entrypoint does, before importing//running
    anything that logs -- records emitted before it runs fall back to
    logging's default handler and come out unformatted.
    """
    global _configured
    if _configured and not force:
        return

    # Imported here, not at module scope: config reads env/.env, and this
    # module gets imported by library code that must stay import-cheap and
    # side-effect free.
    from ofmhelpers.config import settings

    cfg = settings.logging
    logging.config.dictConfig(
        _config(cfg.level.upper(), cfg.format.lower(), cfg.access_log)
    )
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """The one way modules should obtain a logger: `get_logger(__name__)`.

    Deliberately a thin wrapper over `logging.getLogger` -- it exists so the
    convention is greppable and so call sites never import `logging` just to
    make a logger (which is how stray `basicConfig` calls creep in).
    """
    return logging.getLogger(name)
