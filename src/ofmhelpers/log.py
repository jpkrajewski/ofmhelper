import json
import logging
import logging.config
import re
import sys
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ofmhelpers.redact import path_tokens
lazy from ofmhelpers.config import settings

_configured = False

_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | frozenset({"asctime", "message", "taskName"})


_ACCESS_ARGS = 5
_LOGIN_CODE = re.compile(r"([?&]code=)[^&\s]+")


class JsonFormatter(logging.Formatter):
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
        return json.dumps(payload, default=str)


class _HideLoginCodes(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == _ACCESS_ARGS:
            client, method, path, version, status = args
            record.args = (
                client,
                method,
                path_tokens(_LOGIN_CODE.sub(r"\1***", str(path))),
                version,
                status,
            )
        return True


class _HideJobArguments(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for name in _JOB_ARGUMENTS:
            if name in record.__dict__:
                setattr(record, name, _HIDDEN)
        return True


_JOB_ARGUMENTS = ("arguments", "kwargs")
_HIDDEN = "[hidden]"


class _Factory(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    factory: Any = Field(default=None, serialization_alias="()")


class _Formatter(_Factory):
    format: str | None = None
    datefmt: str | None = None


class _Handler(_Factory):
    handler_class: str | None = Field(default=None, serialization_alias="class")
    formatter: str | None = None


class _Logger(BaseModel):
    handlers: list[str] | None = None
    propagate: bool | None = None
    level: str | None = None
    filters: list[str] | None = None


class _Handlers(BaseModel):
    stdout: _Handler
    logfire: _Handler | None = None


class LoggingConfig(BaseModel):
    version: int = 1
    disable_existing_loggers: bool = False
    formatters: dict[str, _Formatter]
    filters: dict[str, _Factory]
    handlers: _Handlers
    root: _Logger
    loggers: dict[str, _Logger]


def _config(
    level: str, fmt: str, access_log: bool, *, logfire: bool = False
) -> LoggingConfig:
    return LoggingConfig(
        formatters={
            "text": _Formatter(
                format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ),
            "json": _Formatter(factory=JsonFormatter),
        },
        filters={
            "hide_login_codes": _Factory(factory=_HideLoginCodes),
            "hide_job_arguments": _Factory(factory=_HideJobArguments),
        },
        handlers=_Handlers(
            stdout=_Handler(
                handler_class="logging.StreamHandler",
                formatter="json" if fmt == "json" else "text",
            ),
            logfire=_Handler(factory="ofmhelpers.monitoring.telemetry.logfire_handler")
            if logfire
            else None,
        ),
        root=_Logger(
            handlers=["stdout", "logfire"] if logfire else ["stdout"], level=level
        ),
        loggers={
            "uvicorn": _Logger(handlers=[], propagate=True),
            "uvicorn.error": _Logger(handlers=[], propagate=True),
            "uvicorn.access": _Logger(
                handlers=[],
                propagate=access_log,
                level="INFO" if access_log else "CRITICAL",
                filters=["hide_login_codes"],
            ),
            "rq": _Logger(level="INFO", propagate=True),
            "rq.worker": _Logger(filters=["hide_job_arguments"], propagate=True),
            "httpx": _Logger(level="WARNING"),
            "httpx2": _Logger(level="WARNING"),
            "httpcore": _Logger(level="WARNING"),
            "urllib3": _Logger(level="WARNING"),
            "requests": _Logger(level="WARNING"),
            "apscheduler": _Logger(level="WARNING"),
            "openpyxl": _Logger(level="WARNING"),
        },
    )


def configure_logging(*, force: bool = False) -> None:
    global _configured
    if _configured and not force:
        return

    cfg = settings.logging
    config = _config(
        cfg.level.upper(),
        cfg.format.lower(),
        cfg.access_log,
        logfire=settings.telemetry.token is not None,
    )
    dumped = config.model_dump(by_alias=True, exclude_none=True)
    dumped["handlers"]["stdout"]["stream"] = sys.stdout
    logging.config.dictConfig(dumped)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
