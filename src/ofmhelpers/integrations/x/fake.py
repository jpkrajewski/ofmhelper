import json
import time
from urllib.parse import urlsplit
lazy from collections.abc import Mapping

from pydantic import BaseModel
lazy import requests

from ofmhelpers.integrations.outbound import FakeReply, FakeRoute
from ofmhelpers.integrations.x.schema import (
    SNOWFLAKE_EPOCH_MS,
    Answer,
    UserResult,
)

HOST = "x.com"
CDN = "https://pbs.twimg.com/fake"
_HOUR_S = 3600
_SNOWFLAKE_TIME_SHIFT = 22


class FakePost(BaseModel):
    views: int
    age_h: float


class FakeXProfile(BaseModel):
    username: str
    user_id: str
    name: str
    followers: int
    posts: list[FakePost] = []


PROFILES = (
    FakeXProfile(
        username="fake_x",
        user_id="1500000000000000001",
        name="Fake X's",
        followers=8_300,
        posts=[
            FakePost(views=12_000, age_h=5),
            FakePost(views=640, age_h=40),
            FakePost(views=99_000, age_h=170),
        ],
    ),
)


def _post_id(age_h: float) -> str:
    posted_ms = int((time.time() - age_h * _HOUR_S) * 1000)
    return str((posted_ms - SNOWFLAKE_EPOCH_MS) << _SNOWFLAKE_TIME_SHIFT)


def _profile(profile: FakeXProfile) -> Answer:
    user = UserResult.model_validate(
        {
            "rest_id": profile.user_id,
            "core": {"screen_name": profile.username, "name": profile.name},
            "relationship_counts": {"followers": profile.followers, "following": 12},
            "avatar": {"image_url": f"{CDN}/avatar_normal.jpg"},
        }
    )
    return Answer.model_validate(
        {"data": {"user_result_by_screen_name": {"result": user}}}
    )


def _timeline(profile: FakeXProfile) -> Answer:
    entries = [
        {
            "content": {
                "content": {
                    "tweet_results": {
                        "result": {
                            "rest_id": _post_id(p.age_h),
                            "views": {"count": str(p.views)},
                            "media_entities2": [
                                {"media_url_https": f"{CDN}/media/{i}.jpg"}
                            ],
                        }
                    }
                }
            }
        }
        for i, p in enumerate(profile.posts)
    ]
    user = UserResult.model_validate(
        {
            "profile_user_originals_timeline": {
                "timeline": {"instructions": [{"entries": entries}]}
            }
        }
    )
    return Answer.model_validate(
        {"data": {"user_result_by_screen_name": {"result": user}}}
    )


def _embedded(answer: Answer) -> str:
    text = json.dumps(answer.model_dump_json(by_alias=True))[1:-1].replace("'", "\\x27")
    return f'{{kind:"Result.Ok",value:"{text}"}}'


def page(profile: FakeXProfile) -> str:
    answers = ",".join(_embedded(a) for a in (_profile(profile), _timeline(profile)))
    return f"<html><body><script>($R=>[{answers}])($R)</script></body></html>"


def _handler(
    request: requests.PreparedRequest, _proxies: Mapping[str, str]
) -> FakeReply:
    name = urlsplit(request.url or "").path.strip("/").lower()
    found = next((p for p in PROFILES if p.username == name), None)
    if found is None:
        return FakeReply.text("<html>This account doesn't exist</html>", status=404)
    return FakeReply.text(page(found))


def routes() -> list[FakeRoute]:
    return [FakeRoute(host=HOST, handler=_handler)]
