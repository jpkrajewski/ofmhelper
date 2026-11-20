lazy from collections.abc import Sequence
lazy from http.cookiejar import Cookie

from ofmhelpers.integrations.cookie_jar import CookieFileError, load_jar
from ofmhelpers.integrations.downloaders.models import (
    CookieJarSummary,
    CookieSite,
    SiteCookies,
)

_INSTAGRAM = "instagram.com"
_INSTAGRAM_LOGIN = "sessionid"


def _is_instagram(domain: str) -> bool:
    host = domain.lstrip(".").lower()
    return host == _INSTAGRAM or host.endswith(f".{_INSTAGRAM}")


def _site(site: CookieSite, found: list[Cookie]) -> SiteCookies:
    mine = [c for c in found if site.owns(c.domain)]
    login = next((c for c in mine if c.name == site.login), None)
    return SiteCookies(
        key=site.key,
        label=site.label,
        icon_url=site.icon_url,
        names=sorted({c.name for c in mine}),
        logged_in=login is not None,
        expires_at=float(login.expires) if login and login.expires else None,
    )


def summarize(cookies: str, sites: Sequence[CookieSite] = ()) -> CookieJarSummary:
    jar = load_jar(cookies)
    found = list(jar)
    if not found:
        msg = "That cookies.txt holds no cookies: export it while logged in"
        raise CookieFileError(msg)
    login = next(
        (c for c in found if c.name == _INSTAGRAM_LOGIN and _is_instagram(c.domain)),
        None,
    )
    return CookieJarSummary(
        cookies=len(found),
        domains=sorted({c.domain.lstrip(".").lower() for c in found}),
        instagram_login=login is not None,
        instagram_expires_at=float(login.expires) if login and login.expires else None,
        sites=[_site(s, found) for s in sites],
        other=sum(1 for c in found if not any(s.owns(c.domain) for s in sites)),
    )
