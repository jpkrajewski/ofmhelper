"""instagram/: what the logged-out GraphQL answers are read as, and which
reels a scan keeps."""

import time

import pytest
import requests
from pydantic import ValidationError

from ofmhelpers.instagram import (
    InstagramClient,
    InstagramError,
    InstagramRateLimitedError,
    InstagramUnavailableError,
    ProfileHandle,
    ProfileNotFoundError,
    ReelDownload,
    scan_profile,
)
from ofmhelpers.instagram.models import (
    ClipsPage,
    MediaInfo,
    MediaPage,
    Profile,
    Reel,
    ReelMedia,
    ReelSummary,
)

CDN = "https://scontent-waw2-2.cdninstagram.com/v/x.mp4"


@pytest.mark.parametrize(
    "raw",
    [
        "sheep_love33",
        "@sheep_love33",
        "https://www.instagram.com/sheep_love33/",
        "instagram.com/Sheep_Love33/reels/",
        "  https://www.instagram.com/sheep_love33?hl=en ",
    ],
)
def test_profile_handle_is_the_bare_username(raw):
    assert ProfileHandle(username=raw).username == "sheep_love33"


@pytest.mark.parametrize("raw", ["", "a b", "https://www.instagram.com/", "x" * 31])
def test_profile_handle_rejects_non_usernames(raw):
    with pytest.raises(ValidationError):
        ProfileHandle(username=raw)


def test_cdn_urls_only():
    assert ReelDownload(code="Dekxh8BJYdu", video_url=CDN)
    assert ReelDownload(code="a", video_url="https://x.fbcdn.net/a.mp4")
    for bad in [
        "http://scontent.cdninstagram.com/a",
        "https://evil.com/a",
        "https://cdninstagram.com.evil.com/a",
    ]:
        with pytest.raises(ValidationError):
            ReelDownload(code="abc", video_url=bad)
    with pytest.raises(ValidationError):
        ReelDownload(code="../x", video_url=CDN)


def test_clips_page_reads_codes_counts_and_cursor():
    page = ClipsPage.model_validate(
        {
            "data": {
                "fetch__XDTUserDict": {
                    "clips_connection": {
                        "edges": [
                            {
                                "node": {
                                    "media": {
                                        "pk": "1",
                                        "code": "A",
                                        "play_count": 10,
                                        "image_versions2": {
                                            "candidates": [{"url": "t"}]
                                        },
                                    }
                                }
                            }
                        ],
                        "page_info": {"end_cursor": "c", "has_next_page": True},
                    }
                }
            }
        }
    )
    assert [(r.code, r.play_count, r.image_versions2.first) for r in page.reels] == [
        ("A", 10, "t")
    ]
    assert page.next_cursor == "c"
    assert ClipsPage.model_validate({"data": {}}).reels == []


@pytest.mark.parametrize(
    ("duration", "seconds"),
    [("PT14.833333S", 14.833333), ("PT1M4.5S", 64.5), ("PT1H", 3600)],
)
def test_media_length_comes_from_the_dash_manifest(duration, seconds):
    page = MediaPage.model_validate(
        {
            "data": {
                "xig_polaris_media": {
                    "if_not_gated_logged_out": {
                        "video_versions": [{"url": CDN}],
                        "video_dash_manifest": f'<MPD mediaPresentationDuration="{duration}">',
                        "taken_at": 5,
                    }
                }
            }
        }
    )
    assert ReelMedia.from_page(page) == ReelMedia(
        video_url=CDN, duration_s=seconds, taken_at=5
    )


def test_null_pinned_field_reads_as_unpinned():
    page = ClipsPage.model_validate(
        {
            "data": {
                "fetch__XDTUserDict": {
                    "clips_connection": {
                        "edges": [
                            {
                                "node": {
                                    "media": {
                                        "pk": "1",
                                        "code": "A",
                                        "clips_tab_pinned_user_ids": None,
                                    }
                                }
                            }
                        ]
                    }
                }
            }
        }
    )
    assert page.reels[0].pinned is False


class _Page:
    def __init__(self, text, status=200):
        self.text, self.status_code, self.ok = text, status, status < 400


def test_empty_profile_page_is_missing_only_when_the_crawler_agrees(monkeypatch):
    client = InstagramClient()
    monkeypatch.setattr(
        client._session, "get", lambda *_a, **_k: _Page("<html>Instagram</html>")
    )
    assert isinstance(client._missing("gone"), ProfileNotFoundError)
    monkeypatch.setattr(
        client._session,
        "get",
        lambda *_a, **_k: _Page('<meta property="og:type" content="profile" />'),
    )
    # The account exists: the empty page was Instagram withholding it.
    assert isinstance(client._missing("throttled"), InstagramRateLimitedError)


def test_gated_media_is_none():
    # What the logged-out query answers for a reel hidden from visitors.
    page = MediaPage.model_validate({"data": {"xig_polaris_media": None}})
    assert ReelMedia.from_page(page) is None


def test_logged_in_media_info():
    info = MediaInfo.model_validate(
        {
            "items": [
                {"video_versions": [{"url": CDN}], "video_duration": 9.5, "taken_at": 7}
            ]
        }
    )
    assert ReelMedia.from_info(info) == ReelMedia(
        video_url=CDN, duration_s=9.5, taken_at=7
    )
    assert ReelMedia.from_info(MediaInfo.model_validate({"items": []})) is None


def test_media_falls_back_to_the_login_for_gated_reels(monkeypatch):
    client = InstagramClient()
    monkeypatch.setattr(
        client, "_query", lambda *_a, **_k: MediaPage.model_validate({"data": {}})
    )
    resolved = ReelMedia(video_url=CDN, duration_s=1, taken_at=0)
    monkeypatch.setattr(client, "media_logged_in", lambda pk: resolved)
    assert client.media("1") == resolved


def test_a_5xx_is_a_transient_failure():
    page = _Page("bad gateway", status=502)
    page.url, page.is_redirect = "https://www.instagram.com/x", False
    with pytest.raises(InstagramUnavailableError):
        InstagramClient._raise_for(page)


def test_calls_retry_drops_and_5xx_with_backoff_and_jitter(monkeypatch):
    monkeypatch.setenv("OFM_IG_HTTP_RETRIES", "4")
    retry = InstagramClient()._session.get_adapter("https://x").max_retries
    assert retry.total == 4
    assert 503 in retry.status_forcelist
    assert retry.backoff_jitter > 0


def test_unreachable_login_leaves_the_reel_login_only(monkeypatch, tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setattr(
        "ofmhelpers.instagram.client.get_cookiefile", lambda: str(cookies)
    )
    client = InstagramClient()

    def drop(*_a, **_k):
        raise requests.exceptions.ConnectionError

    monkeypatch.setattr(client._logged_in, "get", drop)
    assert client.media_logged_in("1") is None


@pytest.mark.parametrize(
    ("page", "exists"),
    [
        ('"pageID":"profilePage"', True),
        ('"pageID":"httpErrorPage"', False),
        ("<html>login</html>", None),
    ],
)
def test_login_tells_a_real_profile_from_a_deleted_one(monkeypatch, page, exists):
    monkeypatch.setattr(
        "ofmhelpers.instagram.client.get_cookiefile", lambda: "cookies.txt"
    )
    client = InstagramClient()
    monkeypatch.setattr(
        type(client), "_logged_in", property(lambda _self: _FakeSession(page))
    )
    assert client.exists_logged_in("someone") is exists


def test_without_a_login_existence_is_unknown(monkeypatch):
    monkeypatch.setattr("ofmhelpers.instagram.client.get_cookiefile", lambda: None)
    assert InstagramClient().exists_logged_in("someone") is None


class _FakeSession:
    def __init__(self, text):
        self.text = text

    def get(self, *_args, **_kwargs):
        return _Page(self.text)


def test_gated_reel_without_cookies_stays_unresolved(monkeypatch):
    monkeypatch.setattr("ofmhelpers.instagram.client.get_cookiefile", lambda: None)
    assert InstagramClient().media_logged_in("1") is None


def test_unresolvable_viral_reel_is_listed_not_dropped():
    client = FakeClient([("gated", 900, None, 1, False), ("ok", 600, 5, 2, False)])
    found = scan_profile(client, "u", 500, 30, 14 * DAY, 100)
    gated = found.reels[0]
    assert (gated.code, gated.video_url, gated.duration_s) == ("gated", None, None)
    # Its date comes from the id when the reel itself could not be read.
    assert abs(gated.taken_at - (time.time() - DAY)) < 60
    assert [r.code for r in found.reels] == ["gated", "ok"]


DAY = 86400


def _pk(days_ago: float) -> str:
    """A media id posted `days_ago` (Instagram ids carry their post time)."""
    ms = (time.time() - days_ago * DAY - 1314220021.721) * 1000
    return str(int(ms) << 23)


def test_reel_shortcode_decodes_to_its_media_id():
    reel = Reel(
        code="Def8WjmhG77",
        taken_at=0,
        duration_s=None,
        views=0,
        followers=0,
        thumbnail_url="",
        video_url=None,
    )
    assert reel.pk == "4008187626229362427"


def test_media_id_carries_its_post_time():
    # A real reel: pk 4009547404732106606 has taken_at 1792195349.
    summary = ReelSummary(pk="4009547404732106606", code="Dekxh8BJYdu")
    assert abs(summary.posted_at - 1792195349) < 60


class FakeClient:
    """Profile of 100 followers; each reel (code, views, length s, days ago,
    pinned). `on_timeline`: the codes the posts timeline also carries (with
    their mp4 + length); the rest only `media` can resolve."""

    def __init__(self, reels, private=False, on_timeline=()):
        self._on_timeline = set(on_timeline)
        self.timeline_reads = 0
        self._summaries = [
            ReelSummary(
                pk=_pk(days_ago),
                code=code,
                play_count=views,
                clips_tab_pinned_user_ids=["9"] if pinned else [],
            )
            for code, views, _length, days_ago, pinned in reels
        ]
        self._lengths = {
            s.pk: (s.code, length)
            for s, (_c, _v, length, _d, _p) in zip(self._summaries, reels, strict=True)
        }
        self._private = private
        self.media_calls = []

    def open_profile(self, username):
        return Profile(
            user_id="9", username=username, followers=100, is_private=self._private
        )

    def reels(self, _user_id):
        yield from self._summaries

    def timeline(self, _username):
        for summary in self._summaries:
            self.timeline_reads += 1
            code, length = self._lengths[summary.pk]
            if code in self._on_timeline and length is not None:
                media = ReelMedia(video_url=CDN, duration_s=length, taken_at=0)
                yield code, media, summary.posted_at, summary.pinned
            else:
                yield code, None, summary.posted_at, summary.pinned

    def media(self, pk):
        """A length of None: a reel nothing could resolve (gated, no login)."""
        code, length = self._lengths[pk]
        self.media_calls.append(code)
        if length is None:
            return None
        return ReelMedia(video_url=CDN, duration_s=length, taken_at=0)


def test_timeline_covers_reels_so_only_the_rest_cost_a_call_each():
    client = FakeClient(
        [
            ("a", 900, 5, 1, False),
            ("b", 800, 6, 2, False),
            ("gated", 700, 7, 3, False),
            *[(f"old{i}", 900, 3, 30 + i, False) for i in range(7)],
            ("never_read", 900, 3, 60, False),
        ],
        on_timeline={"a", "b"},
    )
    found = scan_profile(client, "u", 500, 30, 14 * DAY, 100)
    assert [r.code for r in found.reels] == ["a", "b", "gated"]
    assert client.media_calls == ["gated"]
    # The timeline is read only until seven posts in a row are past the window.
    assert client.timeline_reads == 10


def test_six_unflagged_pins_do_not_end_the_read():
    # A profile can pin 6 reels, and Instagram does not always flag them.
    client = FakeClient(
        [
            *[(f"pin{i}", 10, 5, 100 + i, False) for i in range(6)],
            ("a", 900, 5, 1, False),
            ("b", 800, 6, 2, False),
        ],
        on_timeline={"a", "b"},
    )
    found = scan_profile(client, "u", 500, 30, 14 * DAY, 100)
    assert [r.code for r in found.reels] == ["a", "b"]
    assert client.media_calls == []


def test_scan_keeps_recent_viral_reels_best_first():
    client = FakeClient(
        [
            ("pinned_old", 9000, 3, 60, True),
            ("low", 499, 5, 1, False),
            ("long", 900, 30.1, 2, False),
            ("ok", 500, 30, 3, False),
            ("best", 2000, 16, 13, False),
            ("old", 9000, 3, 15, False),
            ("older", 9000, 3, 20, False),
        ]
    )
    found = scan_profile(
        client, "u", min_views=500, max_duration_s=30, max_age_s=14 * DAY, limit=100
    )
    assert [r.code for r in found.reels] == ["best", "ok"]
    assert found.reels[0].ratio == 20
    # The old pinned reel is skipped; the first old unpinned one ends the scan.
    assert found.scanned == 4
    assert "low" not in client.media_calls
    assert "pinned_old" not in client.media_calls


@pytest.mark.parametrize(
    ("views", "tier"),
    [
        (49_999, None),
        (50_000, "common"),
        (100_000, "uncommon"),
        (200_000, "rare"),
        (500_000, "epic"),
        (999_999, "epic"),
        (1_000_000, "legendary"),
        (8_491_491, "legendary"),
    ],
)
def test_view_tier(views, tier):
    reel = Reel(
        code="a",
        taken_at=0,
        duration_s=5,
        views=views,
        followers=1,
        thumbnail_url="",
        video_url=CDN,
    )
    assert reel.tier == tier


def test_scan_refuses_private_profiles():
    with pytest.raises(InstagramError):
        scan_profile(FakeClient([], private=True), "u", 5, 30, 14 * DAY, 24)


class _Response:
    def __init__(self, status: int, body: dict | None = None, text: str = "") -> None:
        self.status_code = status
        self.ok = status < 400
        self.is_redirect = 300 <= status < 400
        self.text = text
        self.url = "https://www.instagram.com/x"
        self._body = body or {}

    def json(self) -> dict:
        return self._body


# The logged-in web app's page: its viewer and request token.
_LOGGED_IN_PAGE = (
    '"actorID":"999","DTSGInitialData",[],{"token":"dtsg-1"} "LSD",[],{"token":"lsd-1"}'
)


class _LoggedInSession:
    """The uploaded login: GET answers `page`, POSTs answer `pages` in turn."""

    def __init__(self, page: str, pages: list[dict]) -> None:
        self.page = page
        self.pages = pages
        self.sent: list[tuple[str, dict]] = []

    def get(self, _url, **_kwargs):
        return _Response(200, text=self.page)

    def post(self, url, data, **_kwargs):
        self.sent.append((url, data))
        return _Response(200, self.pages.pop(0))


def _clips(codes: list[str], cursor: str | None) -> dict:
    return {
        "data": {
            "fetch__XDTUserDict": {
                "clips_connection": {
                    "edges": [
                        {"node": {"media": {"pk": str(n), "code": c, "play_count": n}}}
                        for n, c in enumerate(codes, 1)
                    ],
                    "page_info": {"end_cursor": cursor, "has_next_page": bool(cursor)},
                }
            }
        }
    }


def _with_login(session) -> InstagramClient:
    client = InstagramClient()
    client.__dict__["_logged_in"] = session
    return client


def test_reels_logged_in_pages_the_reels_tab_as_the_login():
    session = _LoggedInSession(
        _LOGGED_IN_PAGE, [_clips(["a", "b"], "next"), _clips(["c", "d"], "more")]
    )
    reels = _with_login(session).reels_logged_in("dany", "42", limit=3)
    assert [r.code for r in reels] == ["a", "b", "c"]
    url, form = session.sent[0]
    assert url.endswith("/graphql/query")
    assert (form["av"], form["fb_dtsg"], form["lsd"]) == ("999", "dtsg-1", "lsd-1")
    assert '"target_user_id":"42"' in form["variables"]
    assert '"after":"next"' in session.sent[1][1]["variables"]


def test_reels_logged_in_stops_at_the_last_page():
    session = _LoggedInSession(_LOGGED_IN_PAGE, [_clips(["a"], None)])
    assert [r.code for r in _with_login(session).reels_logged_in("d", "42", 10)] == [
        "a"
    ]


def test_reels_logged_in_without_a_working_login_is_none():
    assert _with_login(None).reels_logged_in("d", "42", 10) is None
    # A dead login gets the logged-out page: no viewer on it.
    dead = _LoggedInSession('"actorID":null', [])
    assert _with_login(dead).reels_logged_in("d", "42", 10) is None
    assert dead.sent == []
