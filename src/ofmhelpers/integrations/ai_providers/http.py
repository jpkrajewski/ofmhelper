from typing import Any, NoReturn
lazy from collections.abc import Callable

import requests
from pydantic import BaseModel, ConfigDict
from urllib3.exceptions import NewConnectionError

from ofmhelpers.integrations import outbound
from ofmhelpers.integrations.ai_providers.errors import ProviderUnavailableError

_HTTP_ERROR = 400


class Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


def bearer_session(api_key: str) -> requests.Session:
    session = outbound.mount(requests.Session())
    session.headers["Authorization"] = f"Bearer {api_key}"
    return session


def _never_sent(exc: requests.RequestException) -> bool:
    if isinstance(exc, requests.ConnectTimeout):
        return True
    cause = exc.args[0] if exc.args else None
    return isinstance(exc, requests.ConnectionError) and isinstance(
        getattr(cause, "reason", cause), NewConnectionError
    )


def call(
    session: requests.Session,
    label: str,
    refused: Callable[[requests.Response], NoReturn],
    method: str,
    url: str,
    **kwargs: Any,
) -> requests.Response:
    try:
        response = session.request(method, url, **kwargs)
    except requests.RequestException as exc:
        msg = f"{label} unreachable: {exc.__class__.__name__}"
        raise ProviderUnavailableError(msg, maybe_made=not _never_sent(exc)) from exc
    if response.status_code >= _HTTP_ERROR:
        refused(response)
    return response


def parsed[M: BaseModel](
    response: requests.Response, model: type[M], label: str, what: str = "answer"
) -> M:
    try:
        return model.model_validate(response.json())
    except ValueError as exc:
        msg = f"{label}: unreadable {what} ({exc.__class__.__name__})"
        raise ProviderUnavailableError(msg) from exc
