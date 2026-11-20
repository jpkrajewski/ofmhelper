import json
import logging
import logging.config
import re
import sys
from typing import Any

from ofmhelpers.redact import path_tokens

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


def _config(level: str, fmt: str, access_log: bool) -> dict[str, Any]:
    formatters: dict[str, Any] = {
        "text": {
            "format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "json": {"()": JsonFormatter},
    }

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": formatters,
        "filters": {
            "hide_login_codes": {"()": _HideLoginCodes},
            "hide_job_arguments": {"()": _HideJobArguments},
        },
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "stream": sys.stdout,
                "formatter": "json" if fmt == "json" else "text",
            }
        },
        "root": {"handlers": ["stdout"], "level": level},
        "loggers": {
            "uvicorn": {"handlers": [], "propagate": True},
            "uvicorn.error": {"handlers": [], "propagate": True},
            "uvicorn.access": {
                "handlers": [],
                "propagate": access_log,
                "level": "INFO" if access_log else "CRITICAL",
                "filters": ["hide_login_codes"],
            },
            "rq": {"level": "INFO", "propagate": True},
            "rq.worker": {"filters": ["hide_job_arguments"], "propagate": True},
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
    global _configured
    if _configured and not force:
        return

    from ofmhelpers.config import settings

    cfg = settings.logging
    config = _config(cfg.level.upper(), cfg.format.lower(), cfg.access_log)
    if settings.telemetry.token is not None:
        config["handlers"]["logfire"] = {"()": "ofmhelpers.telemetry.logfire_handler"}
        config["root"]["handlers"].append("logfire")
    logging.config.dictConfig(config)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
