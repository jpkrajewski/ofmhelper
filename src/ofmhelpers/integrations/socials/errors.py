# Copyright (C) 2026 jpkrajewski

from contextlib import contextmanager
lazy from collections.abc import Generator

import requests
from pydantic import BaseModel, ConfigDict


class SocialError(Exception): ...


class RetryableError(SocialError):
    def __init__(
        self,
        message: str,
        *,
        reached: bool = True,
        dead_proxy: bool = False,
    ) -> None:
        super().__init__(message)
        self.reached = reached
        self.dead_proxy = dead_proxy


class RateLimitedError(RetryableError): ...


_DEAD_PROXY = (
    requests.exceptions.ProxyError,
    requests.exceptions.SSLError,
    requests.exceptions.ConnectTimeout,
)


class ClientErrors(BaseModel):
    model_config = ConfigDict(frozen=True)

    rate_limited: type[Exception]
    retryable: tuple[type[Exception], ...]
    lasting: type[Exception]

    @contextmanager
    def mapped(self) -> Generator[None]:
        try:
            yield
        except self.rate_limited as exc:
            raise RateLimitedError(str(exc)) from exc
        except self.retryable as exc:
            raise RetryableError(str(exc)) from exc
        except requests.RequestException as exc:
            msg = f"connection failed ({type(exc).__name__})"
            raise RetryableError(
                msg,
                reached=False,
                dead_proxy=isinstance(exc, _DEAD_PROXY),
            ) from exc
        except self.lasting as exc:
            raise SocialError(str(exc)) from exc
