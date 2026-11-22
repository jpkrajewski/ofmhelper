import time
from typing import Any
lazy from collections.abc import Mapping

import requests
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from ofmhelpers.integrations import outbound
from ofmhelpers.integrations.apify.models import (
    ActorRun,
    DatasetItem,
    ErrorEnvelope,
    LimitsEnvelope,
    RunEnvelope,
)
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

API = "https://api.apify.com/v2"
_WAIT_S = 60
_READ_TIMEOUT_S = 30
_HTTP_UNAUTHORIZED = 401
_HTTP_PAYMENT_REQUIRED = 402
_HTTP_FORBIDDEN = 403
_HTTP_SERVER_ERROR = 500
_READ_TRIES = 5
_READ_RETRY_WAIT_S = 3.0


class ApifyError(Exception): ...


class ApifyAuthError(ApifyError): ...


class ApifyCreditError(ApifyError):
    provider = "Apify"


_ITEMS: TypeAdapter[list[DatasetItem]] = TypeAdapter(list[DatasetItem])


class ApifyClient:
    def __init__(self, token: str) -> None:
        self._token = token
        self._session = outbound.mount(requests.Session())
        self._session.headers["Authorization"] = f"Bearer {token}"

    @property
    def key_hint(self) -> str:
        return f"...{self._token[-6:]}"

    def remaining_usd(self) -> float:
        r = self._session.get(f"{API}/users/me/limits", timeout=_READ_TIMEOUT_S)
        return LimitsEnvelope.model_validate(self._ok(r).json()).data.remaining_usd

    def run(
        self, actor: str, run_input: BaseModel | dict[str, Any], timeout_s: int
    ) -> ActorRun:
        if isinstance(run_input, BaseModel):
            run_input = run_input.model_dump(by_alias=True, exclude_none=True)
        r = self._session.post(
            f"{API}/acts/{actor}/runs",
            params={"timeout": timeout_s},
            json=run_input,
            timeout=_READ_TIMEOUT_S,
        )
        run = RunEnvelope.model_validate(self._ok(r).json()).data
        logger.info("apify %s run %s started (%s)", actor, run.id, self.key_hint)
        deadline = time.monotonic() + timeout_s + _WAIT_S
        while not run.status.finished:
            if time.monotonic() > deadline:
                msg = f"Apify run {run.id} did not finish in {timeout_s} s"
                raise ApifyError(msg)
            r = self._read(
                f"{API}/actor-runs/{run.id}",
                params={"waitForFinish": _WAIT_S},
                timeout=_WAIT_S + _READ_TIMEOUT_S,
            )
            run = RunEnvelope.model_validate(self._ok(r).json()).data
        logger.info("apify run %s %s, $%.4f", run.id, run.status, run.cost_usd)
        return run

    def items(self, run: ActorRun) -> list[DatasetItem]:
        r = self._read(
            f"{API}/datasets/{run.default_dataset_id}/items",
            params={"clean": "true", "format": "json"},
            timeout=_READ_TIMEOUT_S,
        )
        found = self._ok(r).json()
        if not isinstance(found, list):
            msg = "Apify answered a dataset that is not a list"
            raise ApifyError(msg)
        try:
            return _ITEMS.validate_python(found)
        except ValidationError as exc:
            msg = "Apify answered a dataset whose items are not objects"
            raise ApifyError(msg) from exc

    def _read(
        self, url: str, params: Mapping[str, str | int], timeout: float
    ) -> requests.Response:
        for attempt in range(1, _READ_TRIES):
            try:
                r = self._session.get(url, params=params, timeout=timeout)
            except requests.RequestException:
                logger.warning("apify read %d failed", attempt, exc_info=True)
            else:
                if r.status_code < _HTTP_SERVER_ERROR:
                    return r
                logger.warning("apify read %d answered %s", attempt, r.status_code)
            time.sleep(_READ_RETRY_WAIT_S)
        return self._session.get(url, params=params, timeout=timeout)

    @staticmethod
    def _ok(r: requests.Response) -> requests.Response:
        if r.ok:
            return r
        try:
            error = ErrorEnvelope.model_validate_json(r.content).error
        except ValidationError:  # silent: not Apify's error envelope
            error = None
        detail = error.message if error and error.message else r.reason
        logger.warning(
            "apify %s %s: %s %s",
            r.request.method,
            r.url.split("?")[0],
            r.status_code,
            error.type if error else detail,
        )
        if r.status_code == _HTTP_UNAUTHORIZED:
            msg = f"Wrong Apify API key: {detail}"
            raise ApifyAuthError(msg)
        if r.status_code in (_HTTP_PAYMENT_REQUIRED, _HTTP_FORBIDDEN) or (
            error and "usage" in error.type
        ):
            msg = f"No Apify credit left: {detail}"
            raise ApifyCreditError(msg)
        msg = f"Apify error {r.status_code}: {detail}"
        raise ApifyError(msg)


class Funded(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    client: ApifyClient
    credit_usd: float


def by_credit(tokens: list[str]) -> list[Funded]:
    found: list[Funded] = []
    for token in tokens:
        client = ApifyClient(token)
        try:
            remaining = client.remaining_usd()
        except ApifyError, requests.RequestException:
            logger.warning("apify key %s unusable", client.key_hint, exc_info=True)
            continue
        logger.info("apify key %s: $%.2f left", client.key_hint, remaining)
        found.append(Funded(client=client, credit_usd=remaining))
    return sorted(found, key=lambda funded: funded.credit_usd, reverse=True)


def richest(tokens: list[str]) -> ApifyClient:
    found = by_credit(tokens)
    if not found:
        msg = "No usable Apify API key (APIFY_API_KEYS)"
        raise ApifyAuthError(msg)
    return found[0].client
