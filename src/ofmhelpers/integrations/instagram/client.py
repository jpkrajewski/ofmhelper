import json
from functools import cached_property
from http import HTTPStatus
from typing import Any
lazy from collections.abc import Iterator
lazy from pathlib import Path

import requests
from pydantic import BaseModel
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ofmhelpers.config import settings
from ofmhelpers.integrations import outbound
from ofmhelpers.integrations.cookie_jar import CookieFileError, load_jar
from ofmhelpers.integrations.instagram import pages
from ofmhelpers.integrations.instagram.models import (
    ClipsPage,
    GraphQLErrors,
    LoginTokens,
    MediaInfo,
    MediaPage,
    Profile,
    ProfilePage,
    ReelMedia,
    ReelSummary,
    TimelinePage,
    TimelinePost,
)
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

ROOT = "https://www.instagram.com"

_PAGE_HEADERS = frozendict(
    {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
    }
)

_PROFILE_FLAGS = {
    "__relay_internal__pv__PolarisCannesGuardianExperienceEnabledrelayprovider": True,
    "__relay_internal__pv__PolarisCASB976ProfileEnabledrelayprovider": False,
    "__relay_internal__pv__PolarisWebSchoolsEnabledrelayprovider": False,
    "__relay_internal__pv__PolarisRepostsConsumptionEnabledrelayprovider": True,
    "__relay_internal__pv__PolarisShortDramaEnabledrelayprovider": True,
}


class InstagramError(Exception): ...


class InstagramRateLimitedError(InstagramError): ...


class ProfilePrivateError(InstagramError): ...


class InstagramUnavailableError(InstagramError): ...


class ProfileNotFoundError(InstagramError): ...


_RETRY_STATUSES = (500, 502, 503, 504)


def _retrying(session: requests.Session) -> requests.Session:
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
    return outbound.mount(session)


_CRAWLER_UA = (
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
)


class InstagramClient:
    def __init__(self, proxy: str | None = None, cookies: str | None = None) -> None:
        self._cookies = cookies
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
        tokens = pages.reels_page(r.text)
        if tokens.profile_id is None:
            raise self._missing(username)
        self._lsd = tokens.lsd
        self._referer = f"{ROOT}/{username}/reels/"

        page = self._query(
            ProfilePage,
            "PolarisProfilePageContentQuery",
            self._settings.profile_doc_id,
            {
                "enable_integrity_filters": True,
                "id": tokens.profile_id,
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
        r = self._session.get(
            f"{ROOT}/{username}/",
            headers={"User-Agent": _CRAWLER_UA},
            timeout=self._settings.timeout_s,
        )
        if r.ok and not pages.crawler_sees_a_profile(r.text):
            return ProfileNotFoundError("profile not found")
        return InstagramRateLimitedError(
            "Instagram withheld the profile page — retrying later"
        )

    def reels(self, user_id: str) -> Iterator[ReelSummary]:
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

    def timeline(self, username: str) -> Iterator[TimelinePost]:
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
                yield TimelinePost(
                    code=node.code,
                    media=ReelMedia.from_dash(node),
                    posted_at=float(node.taken_at),
                    pinned=bool(node.timeline_pinned_user_ids),
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
        if not self._cookies:
            logger.info("no Instagram login for this call; gated reels stay login-only")
            return None
        try:
            jar = load_jar(self._cookies)
        except CookieFileError:
            logger.warning(
                "the Instagram login is not a readable cookies.txt", exc_info=True
            )
            return None
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
        return pages.is_profile_page(r.text) if r.ok else None

    def _login_tokens(self, username: str) -> LoginTokens | None:
        session = self._logged_in
        if session is None:
            return None
        r = session.get(
            f"{ROOT}/{username}/reels/",
            headers=_PAGE_HEADERS,
            timeout=self._settings.timeout_s,
        )
        tokens = (
            pages.login_tokens(r.text, referer=f"{ROOT}/{username}/reels/")
            if r.ok
            else None
        )
        if tokens is None:
            logger.warning("the uploaded Instagram login is not logged in")
        return tokens

    def reels_logged_in(
        self, username: str, user_id: str, limit: int
    ) -> list[ReelSummary] | None:
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
            body = r.json()
            answer = model.model_validate(body)
            errors = GraphQLErrors.model_validate(body)
        except ValueError as exc:
            logger.warning("instagram %s answered no usable JSON", name, exc_info=True)
            msg = f"Instagram changed its {name} query"
            raise InstagramError(msg) from exc
        if errors.rate_limited:
            logger.warning("instagram %s refused: %s", name, errors.errors[0].message)
            msg = "Instagram is rate-limiting — wait a few minutes"
            raise InstagramRateLimitedError(msg)
        return answer

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
