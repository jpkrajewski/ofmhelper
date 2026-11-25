# Copyright (C) 2026 jpkrajewski

import logging
import re
from http import HTTPStatus

from pydantic import ValidationError

from ofmhelpers.config import settings
from ofmhelpers.integrations import outbound
from ofmhelpers.integrations.snapchat.schema import (
    NextData,
    ProfileProps,
    PublicProfile,
    SnapchatProfile,
    SpotlightPost,
)

logger = logging.getLogger(__name__)

ROOT = "https://www.snapchat.com"
_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


class SnapchatError(Exception): ...


class SnapchatNotFoundError(SnapchatError): ...


class SnapchatNotPublicError(SnapchatError): ...


class SnapchatRateLimitedError(SnapchatError): ...


class SnapchatUnavailableError(SnapchatError): ...


def profile_props(html: str) -> ProfileProps:
    found = _NEXT_DATA.search(html)
    if found is None:
        msg = "Snapchat changed its profile page (no page data)"
        raise SnapchatError(msg)
    try:
        return NextData.model_validate_json(found.group(1)).props.page
    except ValidationError as exc:
        msg = "Snapchat changed its profile page (unreadable page data)"
        raise SnapchatError(msg) from exc


def _posts(username: str, props: ProfileProps) -> list[SpotlightPost]:
    if len(props.highlights) != len(props.spotlights):
        msg = "Snapchat changed its profile page (Spotlight lists differ)"
        raise SnapchatError(msg)
    return [
        SpotlightPost(
            id=h.story_id.value,
            url=f"{ROOT}/@{username}/spotlight/{h.story_id.value}",
            views=s.views,
            posted_at=s.video.uploaded_ms / 1000,
            thumbnail_url=s.video.thumbnail_url,
        )
        for h, s in zip(props.highlights, props.spotlights, strict=False)
        if h.story_id and h.story_id.value and s.video.uploaded_ms > 0
    ]


class SnapchatClient:
    def __init__(self, proxy: str | None = None) -> None:
        self._session = outbound.page_session(settings.snapchat, proxy)

    def profile(self, username: str) -> SnapchatProfile:
        r = self._session.get(
            f"{ROOT}/@{username}",
            timeout=settings.snapchat.timeout_s,
        )
        if r.status_code == HTTPStatus.NOT_FOUND:
            msg = f"@{username} is not on Snapchat"
            raise SnapchatNotFoundError(msg)
        if r.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            msg = "Snapchat asked to slow down"
            raise SnapchatRateLimitedError(msg)
        if r.status_code >= HTTPStatus.BAD_REQUEST:
            msg = f"Snapchat answered {r.status_code}"
            raise SnapchatUnavailableError(msg)
        props = profile_props(r.text)
        if not props.user.case:
            msg = f"@{username} is not on Snapchat"
            raise SnapchatNotFoundError(msg)
        public = props.user.public
        if public is None:
            msg = f"@{username} is not a public profile: Snapchat shows no numbers"
            raise SnapchatNotPublicError(msg)
        return self._profile(public, props)

    @staticmethod
    def _profile(public: PublicProfile, props: ProfileProps) -> SnapchatProfile:
        username = public.username.lower()
        posts = _posts(username, props)
        return SnapchatProfile(
            username=username,
            user_id=public.user_id,
            display_name=public.title,
            subscribers=public.subscribers or None,
            avatar_url=public.avatar_url,
            posts=posts,
            hidden_posts=len(props.highlights) - len(posts),
            complete=not props.cursor,
        )
