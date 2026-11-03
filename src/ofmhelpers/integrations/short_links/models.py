"""What a link provider is to the app (`providers.json`), where a link may
point (`targets.json`), and the shapes every provider client returns."""

import re
from datetime import date
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field


class SlugRules(BaseModel):
    """What a provider takes as a slug. `invalid` matches every run of
    characters it refuses (after lowercasing); `unique_per`: whether a slug
    is taken on the whole provider or per domain."""

    min_length: int = Field(ge=1)
    max_length: int = Field(ge=1)
    invalid: str
    unique_per: Literal["provider", "domain"]

    def make(self, text: str) -> str:
        """`text` (an Instagram handle) as a slug this provider takes:
        refused characters become "-", ends are alphanumeric, padded with
        "-link" when too short, cut when too long."""
        slug = re.sub(self.invalid, "-", text.lower())
        slug = re.sub(r"[^a-z0-9]+$", "", re.sub(r"^[^a-z0-9]+", "", slug))
        slug = slug[: self.max_length].rstrip("-._")
        if len(slug) < self.min_length:
            slug = f"{slug}-link".lstrip("-")
        return slug

    def numbered(self, slug: str, n: int) -> str:
        """`slug` made unique with "-n", still within `max_length`."""
        suffix = f"-{n}"
        return slug[: self.max_length - len(suffix)].rstrip("-._") + suffix


class ProviderInfo(BaseModel):
    """One entry of `providers.json`."""

    key: str = ""
    label: str
    base_url: str
    docs_url: str
    key_prefix: str
    # Short-link domains, the default first.
    domains: list[str] = Field(min_length=1)
    slug: SlugRules
    # Pause between two calls of one job (Oopsie allows 60 a minute).
    min_interval_s: float = Field(ge=0)
    # Links one analytics call covers.
    stats_batch: int = Field(ge=1)
    max_history_days: int = Field(ge=1)
    # Longest range one analytics call still answers in daily buckets.
    max_span_days: int = Field(ge=1)
    # Bouncy's in-app-browser behavior for new links.
    behavior: str | None = None

    @property
    def default_domain(self) -> str:
        return self.domains[0]


class Target(BaseModel):
    """One entry of `targets.json`: a page a link sends visitors to."""

    key: str = ""
    label: str
    # Hostnames (and their subdomains, www. included) of its URLs.
    hosts: list[str] = Field(min_length=1)
    # What the Generate dialog prefills: the URL up to the profile name.
    prefix: str
    # A profile URL's path (a page's name, then anything); the dialog
    # checks the same pattern as it is typed.
    profile: str

    def matches(self, url: str) -> bool:
        """Whether `url` is on one of its hosts (any page)."""
        host = (urlsplit(url).hostname or "").lower()
        return any(host == h or host.endswith(f".{h}") for h in self.hosts)

    def problem(self, url: str) -> str | None:
        """Why `url` is not one of its profiles; None when it is."""
        if not self.matches(url):
            return f"Not a {self.label} link: {url}"
        if not re.fullmatch(self.profile, urlsplit(url).path):
            return f"Add the {self.label} profile after {self.prefix}"
        return None


class NewLink(BaseModel):
    """A link to make: always behind the provider's 18+ confirmation."""

    slug: str
    domain: str
    destination: str
    # Internal name on the provider's dashboard.
    title: str = ""


class RemoteLink(BaseModel):
    """A link as the provider lists it."""

    remote_id: str
    slug: str
    domain: str
    url: str
    destination: str
    active: bool
    # Whether visitors confirm 18+ first; None: the provider does not say.
    adult: bool | None = None
    created_at: float | None = None


class DayStat(BaseModel):
    """One UTC day of one link: landing views and clicks through."""

    day: date
    views: int = Field(ge=0)
    clicks: int = Field(ge=0)


class Usage(BaseModel):
    """The plan's link quota; a limit of None is unlimited."""

    used: int
    limit: int | None
    active: int | None = None
    active_limit: int | None = None

    @property
    def full(self) -> bool:
        return self.limit is not None and self.used >= self.limit

    @property
    def room(self) -> int | None:
        """How many more links can be made live: within the total limit and,
        where there is one, the active limit (Bouncy makes the rest
        inactive, i.e. dead). None: unlimited."""
        free = [
            limit - used
            for limit, used in (
                (self.limit, self.used),
                (self.active_limit, self.active),
            )
            if limit is not None and used is not None
        ]
        return max(min(free), 0) if free else None
