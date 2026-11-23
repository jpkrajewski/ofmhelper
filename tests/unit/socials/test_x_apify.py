# Copyright (C) 2026 jpkrajewski

import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
lazy from collections.abc import Iterator

import pytest
from pydantic import BaseModel

from ofmhelpers.integrations.apify import (
    ActorRun,
    ApifyCreditError,
    ApifyError,
    DatasetItem,
    RunStatus,
)
from ofmhelpers.integrations.socials import ScanTarget, get_platform, scan_through
from ofmhelpers.integrations.socials.platforms import x_apify
from ofmhelpers.integrations.socials.platforms.x_apify import ApifyXSource

AUTHOR = {
    "userName": "DaneWalkerJ",
    "id": "2122028144752308224",
    "name": "Dane Walker Johnson",
    "followers": 1524,
    "following": 36,
    "profilePicture": "https://pbs.twimg.com/profile_images/1/a_normal.jpg",
    "isBlueVerified": False,
}
MOCK = {"type": "mock_tweet", "id": -1, "text": "From KaitoEasyAPI, a reminder"}


def _tweet(post_id: str, hours: float, views: int, **extra: Any) -> dict[str, Any]:
    at = datetime.now(UTC) - timedelta(hours=hours)
    return {
        "type": "tweet",
        "id": post_id,
        "url": f"https://x.com/DaneWalkerJ/status/{post_id}",
        "createdAt": at.strftime("%a %b %d %H:%M:%S +0000 %Y"),
        "viewCount": views,
        "author": AUTHOR,
        "extendedEntities": {
            "media": [
                {"media_url_https": f"https://pbs.twimg.com/media/{post_id}.jpg"},
            ],
        },
        **extra,
    }


class _Client:
    key_hint = "...stub"

    def __init__(
        self,
        items: dict[str, list[dict[str, Any]]],
        fail: frozenset[str] = frozenset(),
        broke: bool = False,  # noqa: FBT001
        status: RunStatus = RunStatus.SUCCEEDED,
    ):
        self.inputs: list[dict[str, Any]] = []
        self._items = items
        self._fail = fail
        self._broke = broke
        self._status = status

    def run(self, _actor: str, run_input: BaseModel, _timeout_s: int) -> ActorRun:
        sent = run_input.model_dump(by_alias=True)
        self.inputs.append(sent)
        if self._broke:
            msg = "No Apify credit left: monthly usage"
            raise ApifyCreditError(msg)
        if sent["from"] in self._fail:
            msg = "Apify error 500: boom"
            raise ApifyError(msg)
        if sent["from"] == "garbled":
            msg = "an answer no model reads"
            raise ValueError(msg)
        if sent["from"] == "killed":
            raise SystemExit(1)
        return ActorRun(id="r", status=self._status, defaultDatasetId=sent["from"])

    def items(self, run: ActorRun) -> list[DatasetItem]:
        return [
            DatasetItem.model_validate(i) for i in self._items[run.default_dataset_id]
        ]


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv("APIFY_API_KEYS", "k1")

    def use(client: _Client, credit: float = 1.0, *more: _Client) -> None:
        funded = [SimpleNamespace(client=c, credit_usd=credit) for c in (client, *more)]
        monkeypatch.setattr(x_apify, "by_credit", lambda _keys: funded)

    return use


def test_a_read_keeps_own_posts_and_drops_mock_rows_and_reposts(keyed):
    client = _Client(
        {
            "danewalkerj": [
                _tweet("2123809704598200564", 11, 230145),
                _tweet("2123526561215234218", 30, 150582),
                _tweet("2123822820019052861", 10, 7590, isReply=True),
                _tweet("2123000000000000001", 5, 99, retweeted_tweet={"id": "1"}),
                MOCK,
                MOCK,
            ],
        },
    )
    keyed(client)
    before = time.time()

    answers = ApifyXSource().scan([ScanTarget(key="7", handle="danewalkerj")])

    stats = answers.stats["7"]
    assert stats.profile.handle == "danewalkerj"
    assert stats.profile.followers == 1524
    assert (
        stats.profile.avatar_url
        == "https://pbs.twimg.com/profile_images/1/a_400x400.jpg"
    )
    assert sorted(p.views for p in stats.posts) == [7590, 150582, 230145]
    assert stats.posts[0].thumbnail_url.startswith("https://pbs.twimg.com/media/")
    assert not stats.complete
    assert stats.since == pytest.approx(before - 7 * 86400, abs=5)
    sent = client.inputs[0]
    assert sent["from"] == "danewalkerj"
    assert sent["queryType"] == "Latest"
    assert int(sent["since_time"]) == pytest.approx(before - 7 * 86400, abs=5)


def test_a_full_page_counts_reposts_and_claims_only_its_oldest_post(keyed, monkeypatch):
    monkeypatch.setenv("OFM_ACCOUNTS_APIFY_X_MAX_ITEMS", "20")
    own = [_tweet(str(2123809704598200000 + i), i + 1, 10) for i in range(12)]
    reposts = [
        _tweet(str(2123809704598300000 + i), i + 1, 10, retweeted_tweet={"id": "1"})
        for i in range(8)
    ]
    keyed(_Client({"danewalkerj": [*own, *reposts, MOCK]}))

    stats = ApifyXSource().scan([ScanTarget(key="7", handle="danewalkerj")]).stats["7"]

    assert len(stats.posts) == 12
    assert stats.since is None
    assert stats.reach == min(p.posted_at for p in stats.posts)


def test_out_of_credit_moves_the_account_to_the_next_key(keyed):
    broke = _Client({}, broke=True)
    rich = _Client({"danewalkerj": [_tweet("2123809704598200564", 1, 5)]})
    keyed(broke, 1.0, rich)

    answers = ApifyXSource().scan([ScanTarget(key="7", handle="danewalkerj")])

    assert set(answers.stats) == {"7"}
    assert len(broke.inputs) == 1
    assert len(rich.inputs) == 1


def test_a_run_that_did_not_succeed_keeps_nothing(keyed):
    keyed(
        _Client(
            {"danewalkerj": [_tweet("2123809704598200564", 1, 5)]},
            status=RunStatus.ABORTED,
        ),
    )
    assert ApifyXSource().scan([ScanTarget(key="7", handle="danewalkerj")]).stats == {}


def test_an_author_without_an_id_is_not_saved(keyed):
    nameless = _tweet("2123809704598200564", 1, 5, author=AUTHOR | {"id": ""})
    keyed(_Client({"danewalkerj": [nameless]}))
    assert ApifyXSource().scan([ScanTarget(key="7", handle="danewalkerj")]).stats == {}


def test_another_batch_spending_holds_this_one_back(keyed, monkeypatch):
    client = _Client({"danewalkerj": [_tweet("2123809704598200564", 1, 5)]})
    keyed(client)

    @contextmanager
    def busy(_keys: int) -> Iterator[bool]:
        yield False

    monkeypatch.setattr(x_apify, "one_apify_batch", busy)
    assert ApifyXSource().scan([ScanTarget(key="7", handle="danewalkerj")]).stats == {}
    assert client.inputs == []


def test_no_posts_in_the_window_leaves_the_account_to_the_page_read(keyed):
    keyed(_Client({"danewalkerj": [MOCK, MOCK]}))
    assert ApifyXSource().scan([ScanTarget(key="7", handle="danewalkerj")]).stats == {}


def test_a_failed_run_leaves_that_account_and_reads_the_next(keyed):
    keyed(
        _Client(
            {
                "good_one": [
                    _tweet("2123809704598200564", 1, 5).copy()
                    | {"author": AUTHOR | {"userName": "good_one"}},
                ],
            },
            fail=frozenset({"bad_one"}),
        ),
    )
    answers = ApifyXSource().scan(
        [ScanTarget(key="1", handle="bad_one"), ScanTarget(key="2", handle="good_one")],
    )
    assert list(answers.stats) == ["2"]


def test_a_key_without_room_for_a_run_reads_nothing(keyed, monkeypatch):
    monkeypatch.setenv("OFM_ACCOUNTS_APIFY_X_USD_PER_RUN", "0.01")
    client = _Client({"danewalkerj": [_tweet("2123809704598200564", 1, 5)]})
    keyed(client, credit=0.015)
    assert ApifyXSource().scan([ScanTarget(key="7", handle="danewalkerj")]).stats == {}
    assert client.inputs == []


def test_one_handle_tracked_by_two_users_is_read_once(keyed):
    client = _Client({"danewalkerj": [_tweet("2123809704598200564", 1, 5)]})
    keyed(client)
    answers = ApifyXSource().scan(
        [
            ScanTarget(key="1", handle="danewalkerj"),
            ScanTarget(key="2", handle="danewalkerj"),
        ],
    )
    assert set(answers.stats) == {"1", "2"}
    assert len(client.inputs) == 1


def test_x_reads_apify_first_then_the_page():
    assert [s.key for s in get_platform("x").sources()] == ["apify", "proxy"]


def _author(name: str) -> dict[str, Any]:
    return AUTHOR | {"userName": name}


def test_a_crash_mid_batch_keeps_the_reads_already_paid(keyed):
    keyed(
        _Client(
            {
                "first_one": [
                    _tweet("2123809704598200564", 1, 5, author=_author("first_one")),
                ],
            },
        ),
    )
    saved: list[str] = []
    targets = [
        ScanTarget(key="1", handle="first_one"),
        ScanTarget(key="2", handle="killed"),
    ]

    with pytest.raises(SystemExit):
        scan_through([ApifyXSource()], targets, lambda key, _stats: saved.append(key))

    assert saved == ["1"]


def test_an_unexpected_error_skips_only_that_account(keyed):
    keyed(
        _Client(
            {
                "after_it": [
                    _tweet("2123809704598200564", 1, 5, author=_author("after_it")),
                ],
            },
        ),
    )
    answers = ApifyXSource().scan(
        [ScanTarget(key="1", handle="garbled"), ScanTarget(key="2", handle="after_it")],
    )
    assert list(answers.stats) == ["2"]


def test_a_timed_out_run_keeps_its_posts_without_claiming_the_window(keyed):
    keyed(
        _Client(
            {"danewalkerj": [_tweet("2123809704598200564", 1, 5)]},
            status=RunStatus.TIMED_OUT,
        ),
    )
    stats = ApifyXSource().scan([ScanTarget(key="7", handle="danewalkerj")]).stats["7"]
    assert stats.since is None


def test_a_row_that_does_not_parse_still_counts_toward_a_full_page(keyed, monkeypatch):
    monkeypatch.setenv("OFM_ACCOUNTS_APIFY_X_MAX_ITEMS", "20")
    own = [_tweet(str(2123809704598200000 + i), i + 1, 10) for i in range(19)]
    broken = {"type": "tweet", "id": "2123809704598299999", "createdAt": "not a date"}
    keyed(_Client({"danewalkerj": [*own, broken, MOCK]}))

    stats = ApifyXSource().scan([ScanTarget(key="7", handle="danewalkerj")]).stats["7"]

    assert len(stats.posts) == 19
    assert stats.since is None
