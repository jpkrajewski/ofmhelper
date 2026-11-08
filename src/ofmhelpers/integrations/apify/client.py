"""Apify's REST API (v2), the calls this app makes: run an actor to the
end, read its dataset, read an account's credit. Through `utils.outbound`,
so OFM_FAKE_EXTERNALS answers it from `apify/fake.py`.

Starting a run is a paid POST: it is sent once, never retried (a lost answer
would start, and pay for, a second run). Reads retry nothing either: a run
is waited for with `waitForFinish`, one long poll at a time."""

import time
from typing import Any

import requests
from pydantic import ValidationError

from ofmhelpers.errors import AppError
from ofmhelpers.integrations.apify.models import (
    ActorRun,
    ErrorEnvelope,
    LimitsEnvelope,
    RunEnvelope,
)
from ofmhelpers.log import get_logger
from ofmhelpers.utils import outbound

logger = get_logger(__name__)

API = "https://api.apify.com/v2"
# How long one status poll may hang server-side (Apify's cap is 60 s).
_WAIT_S = 60
_READ_TIMEOUT_S = 30
_HTTP_UNAUTHORIZED = 401
_HTTP_PAYMENT_REQUIRED = 402
_HTTP_FORBIDDEN = 403
_HTTP_SERVER_ERROR = 500
# Reads of a run already paid for: retried, the run keeps going without us.
_READ_TRIES = 5
_READ_RETRY_WAIT_S = 3.0


class ApifyError(AppError):
    """Apify refused or failed; the message is safe to show."""


class ApifyAuthError(ApifyError):
    """The API token is wrong or revoked."""


class ApifyCreditError(ApifyError):
    """The account has no credit left for this run."""


class ApifyClient:
    """One Apify account (its API token)."""

    def __init__(self, token: str) -> None:
        self._token = token
        self._session = outbound.mount(requests.Session())
        self._session.headers["Authorization"] = f"Bearer {token}"

    @property
    def key_hint(self) -> str:
        """The token's tail, for logs (never the token)."""
        return f"...{self._token[-6:]}"

    def remaining_usd(self) -> float:
        """This month's credit left."""
        r = self._session.get(f"{API}/users/me/limits", timeout=_READ_TIMEOUT_S)
        return LimitsEnvelope.model_validate(self._ok(r)).data.remaining_usd

    def run(self, actor: str, run_input: dict[str, Any], timeout_s: int) -> ActorRun:
        """Runs `actor` (`owner~name`) on `run_input` and waits for it to
        end, at most `timeout_s` (Apify stops it then too)."""
        r = self._session.post(
            f"{API}/acts/{actor}/runs",
            params={"timeout": timeout_s},
            json=run_input,
            timeout=_READ_TIMEOUT_S,
        )
        run = RunEnvelope.model_validate(self._ok(r)).data
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
            run = RunEnvelope.model_validate(self._ok(r)).data
        logger.info("apify run %s %s, $%.4f", run.id, run.status, run.cost_usd)
        return run

    def items(self, run: ActorRun) -> list[dict[str, Any]]:
        """Every item `run` stored in its dataset."""
        r = self._read(
            f"{API}/datasets/{run.default_dataset_id}/items",
            params={"clean": "true", "format": "json"},
            timeout=_READ_TIMEOUT_S,
        )
        found = self._ok(r)
        if not isinstance(found, list):
            msg = "Apify answered a dataset that is not a list"
            raise ApifyError(msg)
        return found

    def _read(self, url: str, **kwargs: Any) -> requests.Response:
        """One GET of a paid run's state or results, tried again when the
        connection drops or Apify answers 5xx: failing the job here pays for
        the whole run again on a retry. The last answer is returned or raised."""
        for attempt in range(1, _READ_TRIES):
            try:
                r = self._session.get(url, **kwargs)
            except requests.RequestException:
                logger.warning("apify read %d failed", attempt, exc_info=True)
            else:
                if r.status_code < _HTTP_SERVER_ERROR:
                    return r
                logger.warning("apify read %d answered %s", attempt, r.status_code)
            time.sleep(_READ_RETRY_WAIT_S)
        return self._session.get(url, **kwargs)

    @staticmethod
    def _ok(r: requests.Response) -> Any:
        """The answer's JSON, or the matching `ApifyError`."""
        if r.ok:
            return r.json()
        try:
            error = ErrorEnvelope.model_validate_json(r.content).error
        except ValidationError:
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


def by_credit(tokens: list[str]) -> list[tuple[ApifyClient, float]]:
    """Every account with its credit left this month, most first; one that
    cannot be read is skipped (logged)."""
    found: list[tuple[ApifyClient, float]] = []
    for token in tokens:
        client = ApifyClient(token)
        try:
            remaining = client.remaining_usd()
        except ApifyError, requests.RequestException:
            logger.warning("apify key %s unusable", client.key_hint, exc_info=True)
            continue
        logger.info("apify key %s: $%.2f left", client.key_hint, remaining)
        found.append((client, remaining))
    return sorted(found, key=lambda pair: pair[1], reverse=True)


def richest(tokens: list[str]) -> ApifyClient:
    """The account with the most credit left this month."""
    found = by_credit(tokens)
    if not found:
        msg = "No usable Apify API key (APIFY_API_KEYS)"
        raise ApifyAuthError(msg)
    return found[0][0]
