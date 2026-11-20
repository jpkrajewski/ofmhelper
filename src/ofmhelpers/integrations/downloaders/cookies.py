import os
import tempfile
from contextlib import contextmanager
from http.cookiejar import LoadError, MozillaCookieJar
from pathlib import Path
lazy from collections.abc import Iterator, Sequence
lazy from http.cookiejar import Cookie

from ofmhelpers.integrations.downloaders.models import (
    CookieJarSummary,
    CookieSite,
    SiteCookies,
)

_INSTAGRAM = "instagram.com"
_INSTAGRAM_LOGIN = "sessionid"


class CookieFileError(ValueError): ...


@contextmanager
def cookie_file(cookies: str | None) -> Iterator[str | None]:
    if not cookies:
        yield None
        return
    fd, path = tempfile.mkstemp(prefix="cookies-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(cookies)
        yield path
    finally:
        Path(path).unlink(missing_ok=True)


def load_jar(cookies: str) -> MozillaCookieJar:
    with cookie_file(cookies) as path:
        jar = MozillaCookieJar()
        try:
            jar.load(path, ignore_discard=True, ignore_expires=True)
        except LoadError, OSError, UnicodeDecodeError:
            msg = (
                "Not a cookies.txt file: export one in Netscape format "
                '(it starts with "# Netscape HTTP Cookie File")'
            )
            raise CookieFileError(msg) from None
    return jar


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
