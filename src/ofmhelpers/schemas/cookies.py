"""A user's own cookies.txt as /cookies shows it: never a value. The sites it
is checked for (`CookieSite`, from the phone catalog's services.json) and
what the check found (`CookieJarSummary`; `integrations/downloaders/cookies.py`
reads the file)."""

from pydantic import BaseModel, ConfigDict


class CookieSite(BaseModel):
    """A site whose login a cookies.txt may carry: its cookie domains (and
    their subdomains) and the cookie that means "logged in". From
    phones/services.json (`LoginService.cookies`)."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    icon_url: str = ""
    domains: list[str]
    login: str

    def owns(self, domain: str) -> bool:
        host = domain.lstrip(".").lower()
        return any(host == d or host.endswith(f".{d}") for d in self.domains)


class SiteCookies(BaseModel):
    """One `CookieSite` in an uploaded cookies.txt: its cookies' names
    (never a value) and whether, and until when, it is logged in."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    icon_url: str = ""
    names: list[str] = []
    logged_in: bool = False
    # When the login cookie expires (epoch s); None without one, or a
    # session cookie.
    expires_at: float | None = None


class CookieJarSummary(BaseModel):
    """What /cookies shows about an uploaded cookies.txt: never a value,
    only which sites it covers and whether it holds an Instagram login."""

    model_config = ConfigDict(extra="forbid")

    # Per known site (services.json), in its order: logged in or not.
    sites: list[SiteCookies] = []
    # Cookies of every other site, only counted.
    other: int = 0

    cookies: int
    # Registrable-ish hosts, leading dot dropped, sorted.
    domains: list[str]
    # A `sessionid` cookie for instagram.com: what every logged-in
    # Instagram call needs.
    instagram_login: bool
    # When that login cookie expires (epoch s); None without one, or a
    # session cookie.
    instagram_expires_at: float | None = None


class SavedCookies(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_name: str
    uploaded_at: float
    summary: CookieJarSummary


class StoredCookies(BaseModel):
    """The stored file itself, value included: only for the code that sends
    or checks it."""

    cookies: str
    file_name: str
    uploaded_at: float
