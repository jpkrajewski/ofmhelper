"""What the client reads off Instagram's HTML pages -- parsing only, no
requests (`client.py` fetches; JSON answers are `models.py`):

- a profile's reels page: the profile id and the `lsd` token every logged-out
  GraphQL call after it needs;
- the same page as the uploaded login loaded it: the viewer id (`av`),
  `fb_dtsg` and `lsd` (`LoginTokens`);
- which page instagram.com rendered (profile or "Sorry, this page isn't
  available");
- the link-preview crawler's view: whether it shows a profile at all.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from ofmhelpers.instagram.models import LoginTokens

_LSD = re.compile(r'"LSD",\[\],\{"token":"([^"]+)"|"lsd":"([^"]+)"')
_PROFILE_ID = re.compile(r'"profile_id":"(\d+)"')
# The logged-in web app's viewer id and request token ("actorID":null on a
# page served logged out).
_ACTOR_ID = re.compile(r'"actorID":"(\d+)"')
_DTSG = re.compile(r'"DTSGInitialData",\[\],\{"token":"([^"]+)"')
# "profilePage", or "httpErrorPage" for "Sorry, this page isn't available"
# (a deleted, banned or renamed account).
_PAGE_ID = re.compile(r'"pageID":"([^"]+)"')
# What a link-preview crawler gets for an existing profile, and only then.
_OG_PROFILE = '<meta property="og:type" content="profile"'


class ReelsPage(BaseModel):
    """A profile's reels page, logged out. No `profile_id`: the page came
    back without the profile (missing account, or data withheld)."""

    profile_id: str | None
    lsd: str


def _lsd(html: str) -> str:
    found = _LSD.search(html)
    return next((g for g in found.groups() if g), "") if found else ""


def reels_page(html: str) -> ReelsPage:
    found = _PROFILE_ID.search(html)
    return ReelsPage(profile_id=found.group(1) if found else None, lsd=_lsd(html))


def login_tokens(html: str, referer: str) -> LoginTokens | None:
    """The login's tokens off a page it loaded; None when the page has no
    viewer (the login is dead)."""
    actor = _ACTOR_ID.search(html)
    dtsg = _DTSG.search(html)
    if actor is None or dtsg is None:
        return None
    return LoginTokens(
        actor_id=actor.group(1), fb_dtsg=dtsg.group(1), lsd=_lsd(html), referer=referer
    )


def is_profile_page(html: str) -> bool | None:
    """True for a profile, False for the "isn't available" page, None when
    the page says neither."""
    found = _PAGE_ID.search(html)
    if found is None:
        return None
    return {"profilePage": True, "httpErrorPage": False}.get(found.group(1))


def crawler_sees_a_profile(html: str) -> bool:
    return _OG_PROFILE in html
