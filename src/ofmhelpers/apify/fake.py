"""Apify's API faked at the wire (OFM_FAKE_EXTERNALS, tests).

Answers the calls `client.ApifyClient` makes the way Apify does: an
account's limits, a run of the Instagram reel scraper (RUNNING when started,
SUCCEEDED at its first poll) and its dataset. The reels are the Instagram
fake's (`instagram/fake.py` profiles, newest first, videos on its fake CDN);
a private one stores the actor's `no_items` error item ("Empty or private
data"), an unknown one its `not_found` item. API keys: `BROKE_KEY` has no
credit left (runs answer 402), `WRONG_KEY` is refused (401), any other
works. `calls` records (method, path) and `inputs` each run's input, for
assertions.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from ofmhelpers.instagram.fake import fake_instagram
from ofmhelpers.utils.outbound import FakeHandler, FakeReply

if TYPE_CHECKING:
    from collections.abc import Mapping

    import requests

HOST = "api.apify.com"
REEL_SCRAPER = "apify~instagram-reel-scraper"
BROKE_KEY = "fake-apify-broke"
WRONG_KEY = "fake-apify-wrong"
# The real actor's Free-plan prices.
USD_PER_REEL = 0.0026
START_USD = 0.001
CREDIT_USD = 5.0


class FakeApify:
    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.datasets: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, str]] = []
        self.inputs: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    # --- the reel scraper --------------------------------------------------

    @staticmethod
    def reel_items(username: str, limit: int) -> list[dict[str, Any]]:
        """What the reel scraper stores for `username`'s newest `limit`."""
        profile = fake_instagram().profiles.get(username)
        if profile is None:
            return [
                {
                    "url": f"https://www.instagram.com/{username}",
                    "error": "not_found",
                    "errorDescription": "Profile does not exist",
                }
            ]
        if profile.is_private:
            return [
                {
                    "url": f"https://www.instagram.com/{username}",
                    "requestErrorMessages": [],
                    "error": "no_items",
                    "errorDescription": "Empty or private data for provided input",
                }
            ]
        rows = fake_instagram().rows(profile)[:limit]
        return [
            {
                "id": row.pk,
                "type": "Video",
                "productType": "clips",
                "shortCode": row.code,
                "url": f"https://www.instagram.com/reel/{row.code}/",
                "videoUrl": row.video,
                "displayUrl": row.thumb,
                "videoPlayCount": row.reel.views,
                "videoViewCount": row.reel.views // 2,
                "likesCount": row.reel.views // 20,
                "commentsCount": row.reel.views // 500,
                "videoDuration": row.reel.duration_s,
                "timestamp": datetime.fromtimestamp(row.taken_at, UTC).isoformat(),
                "ownerUsername": username,
            }
            for row in rows
        ]

    # --- the API -------------------------------------------------------------

    def handle(self, request: requests.PreparedRequest) -> FakeReply:
        url = urlsplit(request.url or "")
        path = url.path
        with self._lock:
            self.calls.append((request.method or "", path))
        token = str(request.headers.get("Authorization") or "").removeprefix("Bearer ")
        if token == WRONG_KEY:
            return _error(401, "user-or-token-not-found", "User was not found")
        if request.method == "GET" and path == "/v2/users/me/limits":
            used = CREDIT_USD if token == BROKE_KEY else 0.0
            return FakeReply.json_body(
                {
                    "data": {
                        "limits": {"maxMonthlyUsageUsd": CREDIT_USD},
                        "current": {"monthlyUsageUsd": used},
                    }
                }
            )
        if request.method == "POST" and path.startswith("/v2/acts/"):
            return self._start(path.split("/")[3], token, request.body)
        if request.method == "GET" and path.startswith("/v2/actor-runs/"):
            return self._poll(path.split("/")[3])
        if request.method == "GET" and path.startswith("/v2/datasets/"):
            items = self.datasets.get(path.split("/")[3])
            if items is None:
                return _error(404, "record-not-found", "Dataset was not found")
            return FakeReply.json_body(items)
        return _error(404, "page-not-found", f"No fake for {request.method} {path}")

    def _start(self, actor: str, token: str, body: Any) -> FakeReply:
        if actor != REEL_SCRAPER:
            return _error(404, "record-not-found", f"Actor {actor} was not found")
        if token == BROKE_KEY:
            return _error(
                402,
                "not-enough-usage-to-run-paid-actor",
                "You have reached your monthly usage limit",
            )
        run_input = json.loads(body or b"{}")
        with self._lock:
            self.inputs.append(run_input)
        items = [
            item
            for username in run_input.get("username", [])
            for item in self.reel_items(
                username, int(run_input.get("resultsLimit", 10))
            )
        ]
        run_id = uuid.uuid4().hex[:17]
        dataset_id = uuid.uuid4().hex[:17]
        reels = [i for i in items if "error" not in i]
        # As on Apify when the run ends: its total still only the start fee,
        # its charged reels one behind (a real 71-reel run: 67).
        run = {
            "id": run_id,
            "status": "RUNNING",
            "defaultDatasetId": dataset_id,
            "usageTotalUsd": START_USD,
            "chargedEventCounts": {"reel": max(len(reels) - 1, 0), "actor-start": 1},
            "pricingInfo": {
                "pricingModel": "PAY_PER_EVENT",
                "pricingPerEvent": {
                    "actorChargeEvents": {
                        "reel": {"eventPriceUsd": USD_PER_REEL},
                        "actor-start": {"eventPriceUsd": START_USD},
                    }
                },
            },
        }
        with self._lock:
            self.runs[run_id] = run
            self.datasets[dataset_id] = items
        return FakeReply.json_body({"data": run}, status=201)

    def _poll(self, run_id: str) -> FakeReply:
        with self._lock:
            run = self.runs.get(run_id)
            if run is None:
                return _error(404, "record-not-found", "Actor run was not found")
            run["status"] = "SUCCEEDED"
            return FakeReply.json_body({"data": dict(run)})


def _error(status: int, kind: str, message: str) -> FakeReply:
    return FakeReply.json_body({"error": {"type": kind, "message": message}}, status)


@lru_cache(maxsize=1)
def fake_apify() -> FakeApify:
    return FakeApify()


def routes() -> dict[str, FakeHandler]:
    def apify(request: requests.PreparedRequest, _: Mapping[str, str]) -> FakeReply:
        return fake_apify().handle(request)

    return {HOST: apify}
