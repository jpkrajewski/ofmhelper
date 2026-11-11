"""Apify's API faked at the wire (OFM_FAKE_EXTERNALS, tests).

Answers the calls `client.ApifyClient` makes the way Apify does: an
account's limits, a run of the Instagram reel scraper or profile scraper
(RUNNING when started, SUCCEEDED at its first poll) and its dataset. The reels are the Instagram
fake's (`instagram/fake.py` profiles, newest first, videos on its fake CDN);
a private one stores the actor's `no_items` error item ("Empty or private
data"), an unknown one its `not_found` item. API keys: `BROKE_KEY` has no
credit left (runs answer 402), `WRONG_KEY` is refused (401), any other
works. `calls` records (method, path) and `inputs` each run's input, for
assertions.
"""

import json
import threading
import time
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit
lazy from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field
lazy import requests

from ofmhelpers.integrations.instagram.fake import CDN, fake_instagram
from ofmhelpers.integrations.outbound import FakeReply, FakeRoute

HOST = "api.apify.com"
REEL_SCRAPER = "apify~instagram-reel-scraper"
PROFILE_SCRAPER = "apify~instagram-profile-scraper"
BROKE_KEY = "fake-apify-broke"
WRONG_KEY = "fake-apify-wrong"
# The real actor's Free-plan prices.
USD_PER_REEL = 0.0026
START_USD = 0.001
CREDIT_USD = 5.0


class _Stored(BaseModel):
    """A dataset item as the actor stores it (its names as aliases)."""

    model_config = ConfigDict(populate_by_name=True)


class FakeItemError(_Stored):
    """What an actor stores for a profile it could not read."""

    url: str
    request_error_messages: list[str] | None = Field(
        default=None, serialization_alias="requestErrorMessages"
    )
    error: str
    error_description: str = Field(serialization_alias="errorDescription")


class FakeProfileItem(_Stored):
    input_url: str = Field(serialization_alias="inputUrl")
    id: str
    username: str
    full_name: str = Field(serialization_alias="fullName")
    followers: int = Field(serialization_alias="followersCount")
    following: int = Field(serialization_alias="followsCount")
    private: bool
    verified: bool
    avatar_url: str = Field(serialization_alias="profilePicUrl")
    avatar_url_hd: str = Field(serialization_alias="profilePicUrlHD")


class FakeReelItem(_Stored):
    id: str
    type: str = "Video"
    product_type: str = Field(default="clips", serialization_alias="productType")
    code: str = Field(serialization_alias="shortCode")
    url: str
    video_url: str = Field(serialization_alias="videoUrl")
    thumbnail_url: str = Field(serialization_alias="displayUrl")
    play_count: int = Field(serialization_alias="videoPlayCount")
    view_count: int = Field(serialization_alias="videoViewCount")
    likes: int = Field(serialization_alias="likesCount")
    comments: int = Field(serialization_alias="commentsCount")
    seconds: float = Field(serialization_alias="videoDuration")
    timestamp: str
    owner: str = Field(serialization_alias="ownerUsername")


def _not_found(username: str) -> FakeItemError:
    return FakeItemError(
        url=f"https://www.instagram.com/{username}",
        error="not_found",
        error_description="Profile does not exist",
    )


class FakeApify:
    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.datasets: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, str]] = []
        self.inputs: list[dict[str, Any]] = []
        # Tests' switches: a key's credit left (default CREDIT_USD), actors
        # whose runs end ABORTED with half their items (credit gone mid-run).
        self.credit_left: dict[str, float] = {}
        self.cut_short: set[str] = set()
        self._lock = threading.Lock()

    # --- the reel scraper --------------------------------------------------

    @staticmethod
    def profile_items(username: str) -> list[FakeProfileItem | FakeItemError]:
        """What the profile scraper stores for `username`."""
        profile = fake_instagram().profiles.get(username)
        if profile is None:
            return [_not_found(username)]
        return [
            FakeProfileItem(
                input_url=f"https://www.instagram.com/{username}",
                id=profile.pk,
                username=profile.username,
                full_name=profile.full_name,
                followers=profile.followers,
                following=profile.following,
                private=profile.is_private,
                verified=profile.is_verified,
                avatar_url=f"{CDN}/avatar-{profile.username}.jpg",
                avatar_url_hd=f"{CDN}/avatar-{profile.username}.jpg",
            )
        ]

    @staticmethod
    def reel_items(
        username: str, limit: int, newer_than_s: float | None = None
    ) -> list[FakeReelItem | FakeItemError]:
        """What the reel scraper stores for `username`'s newest `limit`
        (only those posted after `newer_than_s` when given)."""
        profile = fake_instagram().profiles.get(username)
        if profile is None:
            return [_not_found(username)]
        if profile.is_private:
            return [
                FakeItemError(
                    url=f"https://www.instagram.com/{username}",
                    request_error_messages=[],
                    error="no_items",
                    error_description="Empty or private data for provided input",
                )
            ]
        rows = [
            row
            for row in fake_instagram().rows(profile)
            if newer_than_s is None or row.taken_at >= newer_than_s
        ][:limit]
        return [
            FakeReelItem(
                id=row.pk,
                code=row.code,
                url=f"https://www.instagram.com/reel/{row.code}/",
                video_url=row.video,
                thumbnail_url=row.thumb,
                play_count=row.reel.views,
                view_count=row.reel.views // 2,
                likes=row.reel.views // 20,
                comments=row.reel.views // 500,
                seconds=row.reel.duration_s,
                timestamp=datetime.fromtimestamp(row.taken_at, UTC).isoformat(),
                owner=username,
            )
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
            left = (
                0.0 if token == BROKE_KEY else self.credit_left.get(token, CREDIT_USD)
            )
            used = CREDIT_USD - left
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
        if actor not in (REEL_SCRAPER, PROFILE_SCRAPER):
            return _error(404, "record-not-found", f"Actor {actor} was not found")
        if token == BROKE_KEY:
            return _error(
                402,
                "not-enough-usage-to-run-paid-actor",
                "You have reached your monthly usage limit",
            )
        run_input = json.loads(body or b"{}")
        with self._lock:
            self.inputs.append({"actor": actor, **run_input})
        if actor == PROFILE_SCRAPER:
            stored: list[_Stored] = [
                item
                for username in run_input.get("usernames", [])
                for item in self.profile_items(username)
            ]
        else:
            newer = _newer_than(run_input.get("onlyPostsNewerThan"))
            stored = [
                item
                for username in run_input.get("username", [])
                for item in self.reel_items(
                    username, int(run_input.get("resultsLimit", 10)), newer
                )
            ]
        # As the actor stores them: its names, an error item's unset key absent.
        items = [i.model_dump(by_alias=True, exclude_none=True) for i in stored]
        cut = actor in self.cut_short
        if cut:
            items = items[: len(items) // 2]
        run_id = uuid.uuid4().hex[:17]
        dataset_id = uuid.uuid4().hex[:17]
        reels = [i for i in items if "error" not in i]
        # As on Apify when the run ends: its total still only the start fee,
        # its charged reels one behind (a real 71-reel run: 67).
        run = {
            "id": run_id,
            "status": "RUNNING",
            "_final": "ABORTED" if cut else "SUCCEEDED",
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
        shown = {k: v for k, v in run.items() if k != "_final"}
        return FakeReply.json_body({"data": shown}, status=201)

    def _poll(self, run_id: str) -> FakeReply:
        with self._lock:
            run = self.runs.get(run_id)
            if run is None:
                return _error(404, "record-not-found", "Actor run was not found")
            run["status"] = run["_final"]
            return FakeReply.json_body(
                {"data": {k: v for k, v in run.items() if k != "_final"}}
            )


def _newer_than(value: str | None) -> float | None:
    """The actor's "N days" filter as a timestamp (the only form we send)."""
    if not value:
        return None
    days = int(value.split()[0])
    return time.time() - days * 86400


def _error(status: int, kind: str, message: str) -> FakeReply:
    return FakeReply.json_body({"error": {"type": kind, "message": message}}, status)


@lru_cache(maxsize=1)
def fake_apify() -> FakeApify:
    return FakeApify()


def routes() -> list[FakeRoute]:
    def apify(request: requests.PreparedRequest, _: Mapping[str, str]) -> FakeReply:
        return fake_apify().handle(request)

    return [FakeRoute(host=HOST, handler=apify)]
