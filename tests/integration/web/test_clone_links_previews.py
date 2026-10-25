"""/clone-links Generate's preview over HTTP (`POST /clone-links/previews`,
as the page posts it): one NDJSON line per link with its length, cover and
price, or a fixed reason it cannot be cloned; good reads cached per user,
failed ones not; a cover kept on disk and served by `/thumb/{key}`; every
link answered within the request's deadline; reads limited process-wide.

yt-dlp is faked (`fake_externals`); a test that needs another answer
swaps `video_info` where the preview calls it and counts the reads."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import json
import threading
import time

import pytest
from clone_links_helpers import (
    OTHER,
    REEL,
    client,
    clone_links_env,  # noqa: F401 -- the fixture, used by `pytestmark`
)

from ofmhelpers.downloaders.models import VideoInfo
from ofmhelpers.instagram.fake import CDN
from ofmhelpers.web.clone_links import preview as preview_module
from ofmhelpers.web.clone_links.preview import CANNOT_OPEN, TOO_SLOW, Price
from ofmhelpers.web.stores import cookies as login_cookies
from ofmhelpers.web.stores import users

pytestmark = pytest.mark.usefixtures("clone_links_env")

ANN = "ann@example.com"
BOB = "bob@example.com"
FAILING = "https://www.instagram.com/reel/fake-fail1/"
COOKIES_TXT = (
    b"# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tx\n"
)
LOGIN_WALL = (
    "ERROR: [Instagram] ABC123: The webpage request was redirected to the login "
    "page. You have exceeded the rate-limit for accessing posts anonymously. Use "
    "--cookies-from-browser or --cookies for the authentication."
)
NOT_A_BOT = (
    "ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you're not a bot. "
    "Use --cookies-from-browser or --cookies for the authentication."
)
RESET = (
    "ERROR: [TikTok] 7400000000000000000: Unable to download webpage: "
    "Connection reset by peer"
)


class Reads:
    """`video_info` as the preview calls it: answers `answer(url, logged_in)`
    and records every read (url, logged in, socket timeout)."""

    def __init__(self, answer=None):
        self.answer = answer or (
            lambda url, _logged_in: VideoInfo(url=url, seconds=2.0)
        )
        self.asked: list[tuple[str, bool, float | None]] = []

    def __call__(self, url, cookiefile=None, *, socket_timeout=None):
        self.asked.append((url, cookiefile is not None, socket_timeout))
        return self.answer(url, cookiefile is not None)


@pytest.fixture
def reads(monkeypatch) -> Reads:
    found = Reads()
    monkeypatch.setattr(preview_module, "video_info", found)
    return found


def _previews(c, urls: list[str]) -> list[dict]:
    r = c.post("/clone-links/previews", json={"urls": urls, "provider": "kie"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in r.text.splitlines()]


def _one(c, url: str) -> dict:
    (line,) = _previews(c, [url])
    return line


def _with_cookies(email: str) -> None:
    user = users.by_email(email)
    assert user is not None
    login_cookies.save(user.id, COOKIES_TXT, "cookies.txt")


def test_one_line_per_link_each_ready_and_priced():
    """The real fake read (yt-dlp faked: a 2 s reel, no cover)."""
    lines = _previews(client(), [REEL, OTHER])

    assert sorted(line["url"] for line in lines) == sorted([REEL, OTHER])
    for line in lines:
        assert line["ready"] is True
        assert line["seconds"] == 2.0
        assert line["too_long"] is False
        assert line["credits"] > 0
        assert line["usd"] > 0
        assert line["error"] is None
        assert line["thumb_url"] is None
        assert "error_detail" not in line


def test_the_fake_markers_reach_the_tiles():
    """The fake yt-dlp's own answers: a 20 s reel, a login-only one (no
    cookies uploaded), one with a cover on the fake CDN."""
    long_reel = "https://www.instagram.com/reel/fake-long1/"
    login_reel = "https://www.instagram.com/reel/fake-login1/"
    cover_reel = "https://www.instagram.com/reel/fake-thumb1/"
    c = client()

    lines = {x["url"]: x for x in _previews(c, [long_reel, login_reel, cover_reel])}

    assert (lines[long_reel]["too_long"], lines[long_reel]["ready"]) == (True, False)
    assert lines[long_reel]["seconds"] == 20.0
    assert lines[login_reel]["error"] == (
        "Instagram wants a login: upload your cookies on the Cookies page"
    )
    assert lines[cover_reel]["ready"] is True
    assert c.get(lines[cover_reel]["thumb_url"]).status_code == 200


def test_a_read_has_the_preview_socket_timeout(reads):
    _one(client(), REEL)
    assert reads.asked == [(REEL, False, 10.0)]


def test_a_video_over_the_limit_is_too_long_and_cannot_be_ticked(reads):
    reads.answer = lambda url, _li: VideoInfo(url=url, seconds=20.0)

    line = _one(client(), REEL)

    assert (line["too_long"], line["ready"], line["seconds"]) == (True, False, 20.0)
    assert line["max_s"] == 15


@pytest.mark.parametrize(
    ("seconds", "too_long", "shown"),
    [
        (15.0, True, None),
        (15.04, True, 15.1),
        (14.96, False, 15.0),
        (14.0, False, 14.0),
        (1.1, False, 1.1),
    ],
)
def test_a_length_at_the_limit_is_judged_and_shown_rounded_up(
    reads, seconds, too_long, shown
):
    """A whole-second length (TikTok rounds down) at the limit may be 15.4 s,
    which the worker refuses after the download: too long. The tile never
    reads "15.0 s" next to Too long."""
    reads.answer = lambda url, _li: VideoInfo(url=url, seconds=seconds)

    line = _one(client(), REEL)

    assert line["max_s"] == 15
    assert (line["too_long"], line["seconds"]) == (too_long, shown)
    assert line["ready"] is not too_long


def test_a_failed_read_is_not_cached():
    """The fake yt-dlp fails "fake-fail" links: each look reads again."""
    c = client()
    calls: list[str] = []
    real = preview_module.video_info

    def counted(url, cookiefile=None, *, socket_timeout=None):
        calls.append(url)
        return real(url, cookiefile, socket_timeout=socket_timeout)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(preview_module, "video_info", counted)
        first = _one(c, FAILING)
        second = _one(c, FAILING)

    assert first["error"] == second["error"] == CANNOT_OPEN
    assert first["ready"] is False
    assert calls == [FAILING, FAILING]


def test_a_good_read_is_cached_and_not_read_again(reads):
    c = client()

    first = _one(c, REEL)
    second = _one(c, REEL)

    assert first == second
    assert second["ready"] is True
    assert [url for url, *_ in reads.asked] == [REEL]


def test_the_cache_is_per_user_so_a_login_only_read_stays_its_readers(reads):
    """Ann's cookies read a login-only reel; Bob, without cookies, is told to
    upload his -- not shown Ann's read as ready."""
    reads.answer = lambda url, logged_in: (
        VideoInfo(url=url, seconds=3.0)
        if logged_in
        else VideoInfo(url=url, error=LOGIN_WALL)
    )
    ann = client(ANN)
    _with_cookies(ANN)
    bob = client(BOB)

    assert _one(ann, REEL)["ready"] is True
    seen_by_bob = _one(bob, REEL)
    assert _one(ann, REEL)["ready"] is True

    assert seen_by_bob["ready"] is False
    assert seen_by_bob["error"] == (
        "Instagram wants a login: upload your cookies on the Cookies page"
    )
    assert reads.asked == [(REEL, True, 10.0), (REEL, False, 10.0)]


@pytest.mark.parametrize(
    ("cookies", "url", "error", "logged_out", "said"),
    [
        (False, REEL, LOGIN_WALL, None, "Instagram wants a login"),
        (
            False,
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
            NOT_A_BOT,
            None,
            "YouTube wants a login",
        ),
        (False, REEL, "ERROR: Video unavailable", None, CANNOT_OPEN),
        (False, REEL, "Read timed out. (read timeout=10)", None, "Instagram is busy"),
        (
            True,
            REEL,
            "ERROR: HTTP Error 400: Bad Request",
            LOGIN_WALL,
            "Instagram wants a login",
        ),
        (True, REEL, "ERROR: HTTP Error 404: Not Found", LOGIN_WALL, CANNOT_OPEN),
        (
            True,
            REEL,
            "ERROR: HTTP Error 429: Too Many Requests",
            LOGIN_WALL,
            "Instagram is busy, try again in a few minutes",
        ),
        (
            True,
            "https://www.tiktok.com/@a/video/7400000000000000000",
            RESET,
            None,
            "TikTok is busy",
        ),
    ],
)
def test_a_failed_read_says_what_the_va_can_do(
    reads, cookies, url, error, logged_out, said
):
    """From every try and the clicker's cookies: the first proxy's login wall
    is no reason when the login itself answered 404; the site is named."""
    reads.answer = lambda u, _li: VideoInfo(
        url=u, error=error, logged_out_error=logged_out
    )
    c = client()
    if cookies:
        _with_cookies(ANN)

    r = c.post("/clone-links/previews", json={"urls": [url], "provider": "kie"})
    (line,) = [json.loads(x) for x in r.text.splitlines()]

    assert line["error"].startswith(said)
    assert line["ready"] is False
    assert "HTTP Error" not in r.text
    assert "--cookies" not in r.text


def test_a_cover_is_kept_small_and_served_from_disk(reads):
    reads.answer = lambda url, _li: VideoInfo(
        url=url, seconds=2.0, thumbnail=f"{CDN}/cover.jpg"
    )
    c = client()

    line = _one(c, REEL)
    cover = c.get(line["thumb_url"])

    assert line["thumb_url"].startswith("/clone-links/thumb/")
    assert cover.status_code == 200
    assert cover.headers["content-type"] == "image/webp"


def test_a_cached_read_whose_cover_cannot_be_fetched_is_forgotten(reads):
    """An expired signed cover URL would be retried for the cache's 30 days:
    the next look reads the link again for a fresh one."""
    reads.answer = lambda url, _li: VideoInfo(
        url=url, seconds=2.0, thumbnail=f"{CDN}/gone.png"
    )
    c = client()

    first = _one(c, REEL)
    _one(c, REEL)
    assert len(reads.asked) == 1
    _one(c, REEL)

    assert first["ready"] is True
    assert first["thumb_url"] is None
    assert len(reads.asked) == 2


@pytest.mark.parametrize("key", ["not-hex", "..%2Fetc", "ABCDEF0123456789abcdef01"])
def test_a_cover_key_that_is_not_one_is_404(key):
    assert client().get(f"/clone-links/thumb/{key}").status_code == 404


def test_a_cover_never_made_is_404():
    assert client().get(f"/clone-links/thumb/{'0' * 24}").status_code == 404


def test_every_link_is_answered_by_the_deadline(reads, monkeypatch):
    """A stalled read never keeps Generate grey: by the deadline it is
    "took too long" and the rest are answered."""
    monkeypatch.setenv("OFM_CLONE_LINKS_PREVIEW_DEADLINE_S", "5")
    release = threading.Event()

    def answer(url, _logged_in):
        if url == OTHER:
            release.wait(30)
        return VideoInfo(url=url, seconds=2.0)

    reads.answer = answer
    started = time.monotonic()
    try:
        lines = {line["url"]: line for line in _previews(client(), [REEL, OTHER])}
    finally:
        release.set()

    assert time.monotonic() - started < 15
    assert lines[REEL]["ready"] is True
    assert lines[OTHER]["error"] == TOO_SLOW
    assert lines[OTHER]["ready"] is False


def test_the_read_slots_are_one_for_the_whole_process():
    assert preview_module._read_slots() is preview_module._read_slots()


def test_a_read_waits_for_a_free_slot_and_gives_up_at_its_deadline(reads, monkeypatch):
    """Every read slot taken (other requests reading): a link waits, and at
    its deadline gives up without reading; a freed slot lets it read."""
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(preview_module, "_read_slots", lambda: slots)
    client(ANN)
    user = users.by_email(ANN)
    assert user is not None
    price = Price(credits_per_s=1.0, usd_per_credit=0.1, max_s=15)
    slots.acquire()

    waited = preview_module.preview(
        REEL, user.id, price, "/t", until=time.monotonic() + 0.2
    )
    asked_while_full = list(reads.asked)
    slots.release()
    read = preview_module.preview(
        REEL, user.id, price, "/t", until=time.monotonic() + 5
    )

    assert waited.error == TOO_SLOW
    assert asked_while_full == []
    assert read.ready is True
    assert [url for url, *_ in reads.asked] == [REEL]
    assert slots.acquire(blocking=False)
