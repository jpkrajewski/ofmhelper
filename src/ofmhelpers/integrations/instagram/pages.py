# Copyright (C) 2026 jpkrajewski

import re

from pydantic import BaseModel

from ofmhelpers.integrations.instagram.models import LoginTokens

_LSD = re.compile(r'"LSD",\[\],\{"token":"([^"]+)"|"lsd":"([^"]+)"')
_PROFILE_ID = re.compile(r'"profile_id":"(\d+)"')
_ACTOR_ID = re.compile(r'"actorID":"(\d+)"')
_DTSG = re.compile(r'"DTSGInitialData",\[\],\{"token":"([^"]+)"')
_PAGE_ID = re.compile(r'"pageID":"([^"]+)"')
_OG_PROFILE = '<meta property="og:type" content="profile"'


class ReelsPage(BaseModel):
    profile_id: str | None
    lsd: str


def _lsd(html: str) -> str:
    found = _LSD.search(html)
    return next((g for g in found.groups() if g), "") if found else ""


def reels_page(html: str) -> ReelsPage:
    found = _PROFILE_ID.search(html)
    return ReelsPage(profile_id=found.group(1) if found else None, lsd=_lsd(html))


def login_tokens(html: str, referer: str) -> LoginTokens | None:
    actor = _ACTOR_ID.search(html)
    dtsg = _DTSG.search(html)
    if actor is None or dtsg is None:
        return None
    return LoginTokens(
        actor_id=actor.group(1),
        fb_dtsg=dtsg.group(1),
        lsd=_lsd(html),
        referer=referer,
    )


def is_profile_page(html: str) -> bool | None:
    found = _PAGE_ID.search(html)
    if found is None:
        return None
    return {"profilePage": True, "httpErrorPage": False}.get(found.group(1))


def crawler_sees_a_profile(html: str) -> bool:
    return _OG_PROFILE in html
