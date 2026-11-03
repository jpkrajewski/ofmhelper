"""HTTP for a provider without an SDK: its session, one call, one parsed answer.

Everything here sends a request exactly once. A POST that reached the provider
and lost its answer may have started a paid generation, so nothing in this
module resends it; the only resend of a submit is the runner's, on a rate-limit
refusal (nothing was created).
"""

from collections.abc import Callable
from typing import Any, NoReturn

import requests
from pydantic import BaseModel, ConfigDict

from ofmhelpers.integrations.ai_providers.errors import ProviderUnavailableError
from ofmhelpers.utils import outbound

_HTTP_ERROR = 400


class Lenient(BaseModel):
    """A provider's answer: a field it adds never breaks a parse."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


def bearer_session(api_key: str) -> requests.Session:
    """A session for one account, through `utils.outbound` (OFM_FAKE_EXTERNALS
    answers it from the provider's fake), with no retrying adapter."""
    session = outbound.mount(requests.Session())
    session.headers["Authorization"] = f"Bearer {api_key}"
    return session


def call(
    session: requests.Session,
    label: str,
    refused: Callable[[requests.Response], NoReturn],
    method: str,
    url: str,
    **kwargs: Any,
) -> requests.Response:
    """One request. A transport failure is `ProviderUnavailableError`; an HTTP
    error is whatever the provider's `refused` raises for that answer."""
    try:
        response = session.request(method, url, **kwargs)
    except requests.RequestException as exc:
        msg = f"{label} unreachable: {exc.__class__.__name__}"
        raise ProviderUnavailableError(msg) from exc
    if response.status_code >= _HTTP_ERROR:
        refused(response)
    return response


def parsed[M: BaseModel](
    response: requests.Response, model: type[M], label: str, what: str = "answer"
) -> M:
    """A 2xx answer as `model`. One that is not JSON or not of that shape is the
    provider misbehaving, not the request: `ProviderUnavailableError`, so a poll
    keeps trying and a paid request stays remembered for recovery."""
    try:
        return model.model_validate(response.json())
    except ValueError as exc:  # JSONDecodeError and ValidationError alike
        msg = f"{label}: unreadable {what} ({exc.__class__.__name__})"
        raise ProviderUnavailableError(msg) from exc
