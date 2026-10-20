"""Instagram's logged-out web GraphQL, called the way instagram.com itself
calls it for a visitor who is not signed in: a profile's follower count, its
reels tab with play counts, and one reel's mp4 and length.

No cookies for those: the logged-in REST API redirects a stale session to the
login page and rate-limits anonymous callers within minutes, while these
queries answer a plain visitor. Each needs the page's `lsd` token, read once
from the profile page this client opens first.

The one exception is a reel Instagram hides from logged-out visitors (some
accounts' reels all are): its reels-tab entry still has views, but the
logged-out reel query answers null. For those, `media` falls back to the
logged-in `/api/v1/media/{pk}/info/` with the uploaded cookies.txt (the
downloaders' file, `downloaders.cookies`); without a working login there the
reel stays unresolved (None). The queries are keyed by `doc_id`s
Instagram rotates every few weeks; they are settings
(`settings.instagram.*_doc_id`), and a rotated one shows up here as a
response without data, not as a crash. Undocumented API: when Instagram
changes it, this file and `models.py` are the whole blast radius.
"""

import http.cookiejar
import json
import re
from collections.abc import Iterator
from functools import cached_property
from http import HTTPStatus
from pathlib import Path
from typing import Any

import requests
from pydantic import BaseModel
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ofmhelpers.config import settings
from ofmhelpers.downloaders.cookies import get_cookiefile
from ofmhelpers.instagram.models import (
    ClipsPage,
    LoginTokens,
    MediaInfo,
    MediaPage,
    Profile,
    ProfilePage,
    ReelMedia,
    ReelSummary,
    TimelinePage,
)
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

ROOT = "https://www.instagram.com"

_LSD = re.compile(r'"LSD",\[\],\{"token":"([^"]+)"|"lsd":"([^"]+)"')
_PROFILE_ID = re.compile(r'"profile_id":"(\d+)"')
# The logged-in web app's viewer id and request token, in its page HTML
# ("actorID":null on a page served logged out).
_ACTOR_ID = re.compile(r'"actorID":"(\d+)"')
_DTSG = re.compile(r'"DTSGInitialData",\[\],\{"token":"([^"]+)"')
# Which page instagram.com rendered: "profilePage", or "httpErrorPage" for
# "Sorry, this page isn't available" (a deleted, banned or renamed account).
_PAGE_ID = re.compile(r'"pageID":"([^"]+)"')

_PAGE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-User": "?1",
    "Sec-Fetch-Dest": "document",
}

_PROFILE_FLAGS = {
    "__relay_internal__pv__PolarisCannesGuardianExperienceEnabledrelayprovider": True,
    "__relay_internal__pv__PolarisCASB976ProfileEnabledrelayprovider": False,
    "__relay_internal__pv__PolarisWebSchoolsEnabledrelayprovider": False,
    "__relay_internal__pv__PolarisRepostsConsumptionEnabledrelayprovider": True,
    "__relay_internal__pv__PolarisShortDramaEnabledrelayprovider": True,
}


class InstagramError(Exception):
    """A failure worth showing the user as it is (rate limit, unknown or
    private profile, rotated query id)."""


class InstagramRateLimitedError(InstagramError):
    """Instagram is refusing this IP for now: every next call would fail the
    same way, so a batch should stop rather than burn through its list."""


class ProfilePrivateError(InstagramError):
    """The account is private: nothing of it is visible logged out."""


class InstagramUnavailableError(InstagramError):
    """Instagram answered 5xx even after this client's own retries: a blip
    on its side (or the proxy's), worth another try through another proxy."""


class ProfileNotFoundError(InstagramError):
    """Even the crawler view shows no profile. Usually the account is gone,
    but a proxy IP Instagram has flagged gets that same answer for a real
    account -- so a caller confirms it through another connection and never
    deletes anything on it."""


# Transient answers urllib3 retries itself (with backoff + jitter) before a
# call gives up; 429 is not one -- it is the proxy rotation's job.
_RETRY_STATUSES = (500, 502, 503, 504)


def _retrying(session: requests.Session) -> requests.Session:
    """`session` retrying connection drops, read timeouts and 5xx
    `http_retries` times. Every call here is a read, POSTed GraphQL
    included, so retrying any method is safe."""
    s = settings.instagram
    retry = Retry(
        total=s.http_retries,
        backoff_factor=s.retry_backoff_s,
        backoff_jitter=s.retry_backoff_s,
        status_forcelist=_RETRY_STATUSES,
        allowed_methods=None,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# What a link-preview crawler gets for an existing profile, and only then:
# the one check that tells a missing account from a page that came back
# without its data (a soft rate limit looks exactly like a missing account).
_OG_PROFILE = '<meta property="og:type" content="profile"'
_CRAWLER_UA = (
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
)


class InstagramClient:
    """One profile's worth of calls: `open_profile` first (it reads the
    token every query after it needs), then the queries."""

    def __init__(self, proxy: str | None = None) -> None:
        """`proxy`: the URL every logged-out call of this client goes
        through (`settings.instagram.proxy_urls`), None for direct."""
        self._settings = settings.instagram
        self._session = _retrying(requests.Session())
        self._session.headers["User-Agent"] = self._settings.user_agent
        if proxy:
            self._session.proxies = {"http": proxy, "https": proxy}
        self._lsd = ""
        self._referer = f"{ROOT}/"

    def open_profile(self, username: str) -> Profile:
        r = self._session.get(
            f"{ROOT}/{username}/reels/",
            headers=_PAGE_HEADERS,
            timeout=self._settings.timeout_s,
        )
        self._raise_for(r)
        user_id = _PROFILE_ID.search(r.text)
        if user_id is None:
            raise self._missing(username)
        lsd = _LSD.search(r.text)
        self._lsd = next(g for g in lsd.groups() if g) if lsd else ""
        self._referer = f"{ROOT}/{username}/reels/"

        page = self._query(
            ProfilePage,
            "PolarisProfilePageContentQuery",
            self._settings.profile_doc_id,
            {
                "enable_integrity_filters": True,
                "id": user_id.group(1),
                **_PROFILE_FLAGS,
            },
        )
        user = page.data.user
        if user is None:
            msg = "profile not found (or Instagram changed its query)"
            raise InstagramError(msg)
        return Profile(
            user_id=user.pk,
            username=user.username,
            followers=user.follower_count,
            is_private=user.is_private,
            full_name=user.full_name,
            following=user.following_count,
            is_verified=user.is_verified,
            avatar_url=user.avatar_url,
        )

    def _missing(self, username: str) -> InstagramError:
        """Why the profile page came back without a profile: gone for good
        when the crawler view agrees, else Instagram withholding data for
        now (which a batch treats as a rate limit)."""
        r = self._session.get(
            f"{ROOT}/{username}/",
            headers={"User-Agent": _CRAWLER_UA},
            timeout=self._settings.timeout_s,
        )
        if r.ok and _OG_PROFILE not in r.text:
            return ProfileNotFoundError("profile not found")
        return InstagramRateLimitedError(
            "Instagram withheld the profile page — retrying later"
        )

    def reels(self, user_id: str) -> Iterator[ReelSummary]:
        """The profile's reels tab, newest first; the caller stops iterating
        once it has seen enough."""
        page_size = self._settings.page_size
        cursor = ""
        while True:
            variables = {
                "after": cursor,
                "data": {
                    "include_feed_video": True,
                    "page_size": page_size,
                    "target_user_id": user_id,
                },
                "first": page_size,
                "id": user_id,
                "__relay_internal__pv__PolarisShortDramaEnabledrelayprovider": False,
            }
            page = self._query(
                ClipsPage,
                "PolarisProfileReelsTabContentQuery_connection",
                self._settings.reels_doc_id,
                variables,
            )
            yield from page.reels
            next_cursor = page.next_cursor
            if not next_cursor:
                return
            cursor = next_cursor

    def timeline(
        self, username: str
    ) -> Iterator[tuple[str, ReelMedia | None, float, bool]]:
        """The posts grid, newest first after its pinned posts, as (code, its
        mp4 + length or None for a photo, posted at, pinned) -- twelve posts a
        request, where `media` costs one request per reel. Ends early, without
        error, for an account hidden from logged-out visitors (Instagram
        answers it with an error); `media` covers those."""
        page_size = self._settings.page_size
        cursor: str | None = None
        while True:
            variables = {
                "after": cursor,
                "before": None,
                "data": {
                    "count": page_size,
                    "include_reel_media_seen_timestamp": True,
                    "include_relationship_info": True,
                    "latest_besties_reel_media": True,
                    "latest_reel_media": True,
                },
                "first": page_size,
                "include_multi_captions": True,
                "last": None,
                "username": username,
                "__relay_internal__pv__PolarisMultiCaptionCarouselEnabledrelayprovider": True,
                "__relay_internal__pv__PolarisShortDramaEnabledrelayprovider": False,
                "__relay_internal__pv__PolarisReelsRecoDebugOverlayEnabledrelayprovider": False,
            }
            page = self._query(
                TimelinePage,
                "PolarisProfilePostsTabContentQuery_connection",
                self._settings.timeline_doc_id,
                variables,
            )
            for node in page.nodes:
                yield (
                    node.code,
                    ReelMedia.from_dash(node),
                    float(node.taken_at),
                    bool(node.timeline_pinned_user_ids),
                )
            cursor = page.next_cursor
            if not cursor:
                return

    def media(self, pk: str) -> ReelMedia | None:
        page = self._query(
            MediaPage,
            "PolarisLoggedOutDesktopWWWPostRootContentQuery",
            self._settings.media_doc_id,
            {"media_id": pk},
        )
        return ReelMedia.from_page(page) or self.media_logged_in(pk)

    @cached_property
    def _logged_in(self) -> requests.Session | None:
        """A session carrying the uploaded cookies.txt, or None without one."""
        cookiefile = get_cookiefile()
        if cookiefile is None:
            logger.warning("no cookies.txt uploaded; gated reels stay login-only")
            return None
        jar = http.cookiejar.MozillaCookieJar(cookiefile)
        jar.load(ignore_discard=True, ignore_expires=True)
        session = _retrying(requests.Session())
        session.cookies = jar  # type: ignore[assignment]
        session.headers.update(
            {
                "User-Agent": self._settings.user_agent,
                "X-IG-App-ID": self._settings.app_id,
                "X-ASBD-ID": self._settings.asbd_id,
                "X-Requested-With": "XMLHttpRequest",
                "X-CSRFToken": next(
                    (c.value or "" for c in jar if c.name == "csrftoken"), ""
                ),
                "Referer": f"{ROOT}/",
            }
        )
        return session

    def exists_logged_in(self, username: str) -> bool | None:
        """Whether the uploaded login sees `username`'s profile page: the
        answer a flagged proxy cannot fake. None when there is no working
        login or the page says neither."""
        session = self._logged_in
        if session is None:
            return None
        try:
            r = session.get(
                f"{ROOT}/{username}/",
                headers=_PAGE_HEADERS,
                timeout=self._settings.timeout_s,
            )
        except requests.RequestException:
            logger.warning("logged-in profile %s unreachable", username, exc_info=True)
            return None
        page = _PAGE_ID.search(r.text)
        if not r.ok or page is None:
            return None
        return {"profilePage": True, "httpErrorPage": False}.get(page.group(1))

    def _login_tokens(self, username: str) -> LoginTokens | None:
        """What the uploaded login's GraphQL calls need, read off the
        profile's reels page the way the browser loads it. None when there
        is no login, or Instagram no longer treats it as logged in (the
        page has no viewer)."""
        session = self._logged_in
        if session is None:
            return None
        r = session.get(
            f"{ROOT}/{username}/reels/",
            headers=_PAGE_HEADERS,
            timeout=self._settings.timeout_s,
        )
        actor = _ACTOR_ID.search(r.text)
        dtsg = _DTSG.search(r.text)
        if not r.ok or actor is None or dtsg is None:
            logger.warning("the uploaded Instagram login is not logged in")
            return None
        lsd = _LSD.search(r.text)
        return LoginTokens(
            actor_id=actor.group(1),
            fb_dtsg=dtsg.group(1),
            lsd=next((g for g in lsd.groups() if g), "") if lsd else "",
            referer=f"{ROOT}/{username}/reels/",
        )

    def reels_logged_in(
        self, username: str, user_id: str, limit: int
    ) -> list[ReelSummary] | None:
        """Up to `limit` of the profile's newest reels as the uploaded login
        sees them: every reel, including the ones Instagram hides from
        logged-out visitors (sensitive/age-restricted -- the logged-out tab
        just ends early). The browser's own reels-tab GraphQL query, sent
        with the login; its REST API (`/api/v1/clips/user/`) redirects
        cookie sessions to the home page (2026-10-20). None without a
        working login, so the caller falls back to the logged-out tab."""
        tokens = self._login_tokens(username)
        if tokens is None:
            return None
        found: list[ReelSummary] = []
        cursor: str | None = None
        page_size = self._settings.page_size
        while len(found) < limit:
            variables = {
                "after": cursor,
                "before": None,
                "data": {
                    "include_feed_video": True,
                    "page_size": page_size,
                    "target_user_id": user_id,
                },
                "first": page_size,
                "last": None,
                "id": user_id,
                "__relay_internal__pv__PolarisShortDramaEnabledrelayprovider": False,
            }
            page = self._query(
                ClipsPage,
                "PolarisProfileReelsTabContentQuery_connection",
                self._settings.reels_doc_id,
                variables,
                login=tokens,
            )
            found.extend(page.reels)
            cursor = page.next_cursor
            if not cursor:
                break
        return found[:limit]

    def media_logged_in(self, pk: str) -> ReelMedia | None:
        """A logged-out-gated reel, through the uploaded login. None when
        there is none or it no longer works (Instagram redirects a dead
        session to its home page) -- the scan still lists the reel."""
        session = self._logged_in
        if session is None:
            return None
        try:
            r = session.get(
                f"{ROOT}/api/v1/media/{pk}/info/",
                timeout=self._settings.timeout_s,
                allow_redirects=False,
            )
        except requests.RequestException:
            # One reel left login-only, not the whole scan failed.
            logger.warning("logged-in media %s unreachable", pk, exc_info=True)
            return None
        if r.status_code != HTTPStatus.OK:
            logger.info("logged-in media %s -> %s", pk, r.status_code)
            return None
        return ReelMedia.from_info(MediaInfo.model_validate(r.json()))

    def download_video(self, url: str, dest: Path) -> Path:
        with self._session.get(url, stream=True, timeout=self._settings.timeout_s) as r:
            r.raise_for_status()
            with dest.open("wb") as out:
                for chunk in r.iter_content(1 << 20):
                    out.write(chunk)
        return dest

    def _query[M: BaseModel](
        self,
        model: type[M],
        name: str,
        doc_id: str,
        variables: dict[str, Any],
        login: LoginTokens | None = None,
    ) -> M:
        """One GraphQL query: logged out (this client's session, through its
        proxy), or -- with `login` -- as the uploaded login, the way the
        logged-in web app sends it."""
        session = self._logged_in if login else self._session
        if session is None:
            msg = "no Instagram login uploaded"
            raise InstagramError(msg)
        lsd = login.lsd if login else self._lsd
        form = {
            "av": login.actor_id if login else "0",
            "__d": "www",
            "__user": "0",
            "__a": "1",
            "__comet_req": "7",
            "lsd": lsd,
            "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": name,
            "server_timestamps": "true",
            "variables": json.dumps(variables, separators=(",", ":")),
            "doc_id": doc_id,
        }
        if login:
            form["fb_dtsg"] = login.fb_dtsg
        r = session.post(
            f"{ROOT}/graphql/query" if login else f"{ROOT}/api/graphql",
            headers={
                "Accept": "*/*",
                "X-FB-Friendly-Name": name,
                "X-FB-LSD": lsd,
                "X-IG-App-ID": self._settings.app_id,
                "X-ASBD-ID": self._settings.asbd_id,
                "Origin": ROOT,
                "Referer": login.referer if login else self._referer,
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
            },
            data=form,
            timeout=self._settings.timeout_s,
            allow_redirects=False,
        )
        self._raise_for(r)
        try:
            return model.model_validate(r.json())
        except ValueError as exc:
            logger.warning("instagram %s answered no usable JSON", name, exc_info=True)
            msg = f"Instagram changed its {name} query"
            raise InstagramError(msg) from exc

    @staticmethod
    def _raise_for(r: requests.Response) -> None:
        if r.status_code == HTTPStatus.TOO_MANY_REQUESTS or (
            r.status_code == HTTPStatus.UNAUTHORIZED and "wait a few minutes" in r.text
        ):
            msg = "Instagram is rate-limiting — wait a few minutes"
            raise InstagramRateLimitedError(msg)
        if r.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
            logger.warning("instagram %s -> %s", r.url, r.status_code)
            msg = f"Instagram answered {r.status_code}"
            raise InstagramUnavailableError(msg)
        if r.status_code == HTTPStatus.NOT_FOUND:
            msg = "profile not found"
        elif not r.ok or r.is_redirect:
            logger.warning("instagram %s -> %s", r.url, r.status_code)
            msg = f"Instagram answered {r.status_code}"
        else:
            return
        raise InstagramError(msg)
