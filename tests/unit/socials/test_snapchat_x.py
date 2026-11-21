import pytest
import requests

from ofmhelpers.integrations import outbound
from ofmhelpers.integrations.outbound import FakeReply, FakeRoute
from ofmhelpers.integrations.snapchat import fake as snapchat_fake
from ofmhelpers.integrations.snapchat.fake import routes as snapchat_routes
from ofmhelpers.integrations.socials import (
    RateLimitedError,
    RetryableError,
    SocialError,
    get_platform,
    platforms,
)
from ofmhelpers.integrations.x.client import embedded_answers
from ofmhelpers.integrations.x.fake import routes as x_routes


@pytest.fixture
def wire(monkeypatch):
    monkeypatch.setenv("OFM_FAKE_EXTERNALS", "1")
    outbound.register_fakes([*snapchat_routes(), *x_routes()])

    def answer(host: str, reply: FakeReply) -> None:
        outbound.register_fakes(
            [FakeRoute(host=host, handler=lambda _request, _proxies: reply)]
        )

    yield answer
    outbound.register_fakes([*snapchat_routes(), *x_routes()])


@pytest.mark.parametrize(
    "raw",
    [
        "DaneWalkerJohn",
        "@danewalkerjohn",
        "https://www.snapchat.com/add/danewalkerjohn",
        "snapchat.com/@DaneWalkerJohn/spotlight/W7_abc",
        " https://www.snapchat.com/@danewalkerjohn?share_id=1 ",
    ],
)
def test_a_snapchat_handle_is_read_from_a_name_or_a_link(raw):
    snap = get_platform("snapchat")
    assert snap.handle(raw) == "danewalkerjohn"
    assert (
        snap.profile_url("danewalkerjohn") == "https://www.snapchat.com/@danewalkerjohn"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "DanewalkerJ",
        "@DanewalkerJ",
        "https://x.com/DanewalkerJ",
        "https://twitter.com/danewalkerj/status/2123526561215234218",
        "x.com/DanewalkerJ?s=20",
    ],
)
def test_an_x_handle_is_read_from_a_name_or_a_link(raw):
    x = get_platform("x")
    assert x.handle(raw) == "danewalkerj"
    assert x.profile_url("danewalkerj") == "https://x.com/danewalkerj"


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("snapchat", "1abc"),
        ("snapchat", "ab"),
        ("snapchat", "a" * 16),
        ("snapchat", "<script>"),
        ("x", "a" * 16),
        ("x", "dane.walker"),
        ("x", ""),
        ("x", "https://x.com/"),
    ],
)
def test_junk_is_not_a_handle(key, raw):
    with pytest.raises(ValueError, match="username"):
        get_platform(key).handle(raw)


def test_snapchat_and_x_read_logged_out_only():
    assert {"instagram", "snapchat", "x"} <= set(platforms())
    assert [s.key for s in get_platform("snapchat").sources()] == ["proxy"]
    assert "login" not in [s.key for s in get_platform("x").sources()]


def test_a_public_snapchat_profile_gives_subscribers_and_spotlight_views(wire):
    stats = get_platform("snapchat").fetch("fake_snap", None)
    assert stats.profile.handle == "fake_snap"
    assert stats.profile.followers == 12_400
    assert stats.profile.account_id == "host-fake_snap"
    assert sorted(p.views for p in stats.posts) == [880, 5200, 31_000]
    assert all(
        p.url.startswith("https://www.snapchat.com/@fake_snap/spotlight/")
        for p in stats.posts
    )
    assert not stats.profile.followers_hidden
    assert stats.hidden_posts == 1
    assert stats.metrics().hidden_posts == 1
    assert stats.complete
    assert stats.reach == 0


def test_every_spotlight_shown_hides_none(wire, monkeypatch):
    shown = snapchat_fake.PROFILES[0].model_copy(update={"hidden": 0})
    monkeypatch.setattr(snapchat_fake, "PROFILES", (shown,))
    stats = get_platform("snapchat").fetch("fake_snap", None)
    assert len(stats.posts) == 3
    assert stats.hidden_posts == 0
    assert stats.complete


def _snap_page(subscribers: str, cursor: str = "") -> str:
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"props":{"pageProps":{"userProfile":{"$case":"publicProfileInfo",'
        f'"publicProfileInfo":{{"username":"a_b","subscriberCount":"{subscribers}"}}}},'
        '"spotlightHighlights":[{"storyId":{"value":"s1"}},{"storyId":null},'
        '{"storyId":{"value":"s2"}}],'
        '"spotlightStoryMetadata":[{"videoMetadata":{"uploadDateMs":"1795000000000",'
        '"viewCount":"40"}},{"videoMetadata":{"uploadDateMs":"0"}},'
        '{"videoMetadata":{"uploadDateMs":"1794000000000","viewCount":"7"}}],'
        f'"spotlightHighlightsCursor":"{cursor}"}}}}}}</script>'
    )


def test_a_subscriber_count_of_zero_is_hidden_not_zero(wire):
    wire("www.snapchat.com", FakeReply.text(_snap_page("0")))
    stats = get_platform("snapchat").fetch("a_b", None)
    assert stats.profile.followers_hidden
    assert stats.profile.followers == 0
    assert [p.views for p in stats.posts] == [40, 7]
    assert stats.hidden_posts == 1


def test_more_spotlights_to_load_is_not_complete(wire):
    wire("www.snapchat.com", FakeReply.text(_snap_page("3", cursor="next")))
    stats = get_platform("snapchat").fetch("a_b", None)
    assert not stats.profile.followers_hidden
    assert stats.profile.followers == 3
    assert not stats.complete
    assert stats.reach == 1_794_000_000


def test_spotlight_lists_of_different_length_refuse_the_read(wire):
    page = (
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"props":{"pageProps":{"userProfile":{"$case":"publicProfileInfo",'
        '"publicProfileInfo":{"username":"a_b","subscriberCount":"3"}},'
        '"spotlightHighlights":[{"storyId":{"value":"s1"}}],'
        '"spotlightStoryMetadata":[]}}}</script>'
    )
    wire("www.snapchat.com", FakeReply.text(page))
    with pytest.raises(SocialError, match="Spotlight lists differ"):
        get_platform("snapchat").fetch("a_b", None)


def test_a_snapchat_page_without_a_user_is_not_found(wire):
    page = (
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"props":{"pageProps":{}}}</script>'
    )
    wire("www.snapchat.com", FakeReply.text(page))
    with pytest.raises(RetryableError, match="is not on Snapchat"):
        get_platform("snapchat").fetch("a_b", None)


def test_an_x_profile_gives_followers_and_post_views(wire):
    stats = get_platform("x").fetch("fake_x", None)
    assert stats.profile.handle == "fake_x"
    assert stats.profile.display_name == "Fake X's"
    assert stats.profile.followers == 8_300
    assert stats.profile.avatar_url == "https://pbs.twimg.com/fake/avatar_400x400.jpg"
    assert sorted(p.views for p in stats.posts) == [640, 12_000, 99_000]
    newest = max(stats.posts, key=lambda p: p.posted_at)
    assert newest.url == f"https://x.com/fake_x/status/{newest.id}"
    assert not stats.complete
    assert stats.reach == min(p.posted_at for p in stats.posts)


def test_a_snapchat_account_that_is_not_public_is_a_lasting_refusal(wire):
    with pytest.raises(SocialError, match="not a public profile") as caught:
        get_platform("snapchat").fetch("fake_hidden", None)
    assert not isinstance(caught.value, RetryableError)


@pytest.mark.parametrize("key", ["snapchat", "x"])
def test_a_missing_account_is_tried_again_later(wire, key):
    with pytest.raises(RetryableError, match="is not on") as caught:
        get_platform(key).fetch("nobody_here", None)
    assert caught.value.reached


@pytest.mark.parametrize(
    ("key", "host"), [("snapchat", "www.snapchat.com"), ("x", "x.com")]
)
def test_a_429_stops_the_batch(wire, key, host):
    wire(host, FakeReply.text("slow down", status=429))
    with pytest.raises(RateLimitedError):
        get_platform(key).fetch("someone", None)


@pytest.mark.parametrize(
    ("key", "host"), [("snapchat", "www.snapchat.com"), ("x", "x.com")]
)
def test_a_5xx_is_tried_again_later(wire, key, host, monkeypatch):
    monkeypatch.setenv("OFM_SNAPCHAT_HTTP_RETRIES", "0")
    monkeypatch.setenv("OFM_X_HTTP_RETRIES", "0")
    wire(host, FakeReply.text("<html>oops</html>", status=503))
    with pytest.raises(RetryableError, match="answered 503"):
        get_platform(key).fetch("someone", None)


@pytest.mark.parametrize(
    ("key", "host"), [("snapchat", "www.snapchat.com"), ("x", "x.com")]
)
def test_a_page_without_its_data_is_a_refusal_that_says_why(wire, key, host):
    wire(host, FakeReply.text("<html><body>Log in to see this</body></html>"))
    with pytest.raises(SocialError, match="changed its") as caught:
        get_platform(key).fetch("someone", None)
    assert not isinstance(caught.value, RetryableError)


def test_unreadable_snapchat_page_data_is_a_refusal(wire):
    wire(
        "www.snapchat.com",
        FakeReply.text(
            '<script id="__NEXT_DATA__" type="application/json">{"props": 1}</script>'
        ),
    )
    with pytest.raises(SocialError, match="unreadable page data"):
        get_platform("snapchat").fetch("someone", None)


@pytest.mark.parametrize("key", ["snapchat", "x"])
def test_a_dropped_connection_never_got_there(key, monkeypatch):
    def refuse(*_args, **_kwargs):
        msg = "down"
        raise requests.ConnectionError(msg)

    monkeypatch.setattr(requests.Session, "get", refuse)
    with pytest.raises(RetryableError) as caught:
        get_platform(key).fetch("someone", "http://p:1")
    assert not caught.value.reached


def test_js_escapes_in_an_embedded_answer_are_decoded():
    value = r"{\"data\":{\"user_result_by_screen_name\":{\"result\":{\"rest_id\":\"7\",\"core\":{\"screen_name\":\"a\",\"name\":\"It\x27s é 😀 \\\\x27\"},\"relationship_counts\":{\"followers\":3}}}}}"
    [answer] = embedded_answers(f'x{{kind:"Result.Ok",value:"{value}"}}y')
    user = answer.data.user.result
    assert user.core.name == "It's é \U0001f600 \\x27"
    assert user.counts.followers == 3


def test_an_embedded_answer_that_is_not_json_is_skipped():
    assert embedded_answers('{kind:"Result.Ok",value:"not json"}') == []
