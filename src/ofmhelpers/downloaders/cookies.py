"""A cookies.txt (Netscape format) held as text, never as a shared file.

Each user's own cookies live encrypted in Postgres (`web/stores/cookies.py`);
the code that needs a jar gets the text from its caller and turns it into
what its tool takes here: a temporary file for yt-dlp / gallery-dl
(`cookie_file`, deleted on exit, mode 0600) or an `http.cookiejar` for
`requests` (`load_jar`). Nothing here logs a cookie value.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from http.cookiejar import LoadError, MozillaCookieJar
from pathlib import Path
from typing import TYPE_CHECKING

from ofmhelpers.downloaders.models import CookieJarSummary, CookieSite, SiteCookies

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from http.cookiejar import Cookie

_INSTAGRAM = "instagram.com"
_INSTAGRAM_LOGIN = "sessionid"


class CookieFileError(ValueError):
    """Not a cookies.txt anything here can read: the message says why, in
    words the person uploading it can act on."""


@contextmanager
def cookie_file(cookies: str | None) -> Iterator[str | None]:
    """`cookies` as a file only this process user can read, for the
    duration of the block (yt-dlp may write it back; that copy is
    discarded). None in, None out: run without a login."""
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
    """The parsed jar. Raises CookieFileError when it is not Netscape
    cookies.txt."""
    with cookie_file(cookies) as path:
        jar = MozillaCookieJar()
        try:
            jar.load(path, ignore_discard=True, ignore_expires=True)
        except (LoadError, OSError, UnicodeDecodeError) as exc:
            msg = (
                "Not a cookies.txt file: export one in Netscape format "
                '(it starts with "# Netscape HTTP Cookie File")'
            )
            raise CookieFileError(msg) from exc
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
    """Which sites `cookies` covers, whether it logs into Instagram, and per
    known site (`sites`) its cookies' names and login. Raises
    CookieFileError for a file that is not cookies.txt or holds no
    cookie."""
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
