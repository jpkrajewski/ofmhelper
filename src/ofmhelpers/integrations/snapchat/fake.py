# Copyright (C) 2026 jpkrajewski

import time
from urllib.parse import urlsplit
lazy from collections.abc import Mapping

from pydantic import BaseModel
lazy import requests

from ofmhelpers.integrations.outbound import FakeReply, FakeRoute
from ofmhelpers.integrations.snapchat.schema import (
    Highlight,
    NextData,
    ProfileProps,
    PublicProfile,
    SpotlightMetadata,
    UserProfile,
)

HOST = "www.snapchat.com"
CDN = "https://cf-st.sc-cdn.net/fake"
_HOUR_S = 3600


class FakeSpotlight(BaseModel):
    views: int
    age_h: float


class FakeSnapProfile(BaseModel):
    username: str
    title: str
    subscribers: int
    spotlights: list[FakeSpotlight] = []
    hidden: int = 1
    public: bool = True


PROFILES = (
    FakeSnapProfile(
        username="fake_snap",
        title="Fake Snap",
        subscribers=12_400,
        spotlights=[
            FakeSpotlight(views=5200, age_h=20),
            FakeSpotlight(views=880, age_h=60),
            FakeSpotlight(views=31_000, age_h=200),
        ],
    ),
    FakeSnapProfile(
        username="fake_hidden",
        title="Hidden",
        subscribers=0,
        public=False,
    ),
)


def _user(profile: FakeSnapProfile) -> UserProfile:
    if not profile.public:
        return UserProfile.model_validate({"$case": "userInfo"})
    public = PublicProfile.model_validate(
        {
            "username": profile.username,
            "title": profile.title,
            "subscriberCount": str(profile.subscribers),
            "profilePictureUrl": f"{CDN}/{profile.username}.jpg",
            "hostUserId": f"host-{profile.username}",
        },
    )
    return UserProfile.model_validate(
        {"$case": "publicProfileInfo", "publicProfileInfo": public},
    )


def _spotlight(spot: FakeSpotlight, index: int) -> SpotlightMetadata:
    return SpotlightMetadata.model_validate(
        {
            "videoMetadata": {
                "uploadDateMs": int((time.time() - spot.age_h * _HOUR_S) * 1000),
                "viewCount": spot.views,
                "thumbnailUrl": f"{CDN}/{index}.jpg",
            },
            "engagementStats": {"viewCount": spot.views},
        },
    )


def _highlight(username: str, index: int) -> Highlight:
    return Highlight.model_validate({"storyId": {"value": f"{username}-spot-{index}"}})


def page(profile: FakeSnapProfile) -> str:
    spots = list(enumerate(profile.spotlights))
    props = ProfileProps.model_validate(
        {
            "userProfile": _user(profile),
            "spotlightHighlights": [
                *(Highlight() for _ in range(profile.hidden)),
                *(_highlight(profile.username, i) for i, _ in spots),
            ],
            "spotlightStoryMetadata": [
                *(SpotlightMetadata() for _ in range(profile.hidden)),
                *(_spotlight(s, i) for i, s in spots),
            ],
        },
    )
    data = NextData.model_validate({"props": {"pageProps": props}})
    return (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        f"{data.model_dump_json(by_alias=True)}</script></body></html>"
    )


def _handler(
    request: requests.PreparedRequest,
    _proxies: Mapping[str, str],
) -> FakeReply:
    name = urlsplit(request.url or "").path.strip("/").removeprefix("@").lower()
    found = next((p for p in PROFILES if p.username == name), None)
    if found is None:
        return FakeReply.text("<html>Not found</html>", status=404)
    return FakeReply.text(page(found))


def routes() -> list[FakeRoute]:
    return [FakeRoute(host=HOST, handler=_handler)]
