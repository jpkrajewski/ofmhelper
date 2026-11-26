# Copyright (C) 2026 jpkrajewski

from pydantic import BaseModel, ConfigDict


class CookieSite(BaseModel):
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
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    icon_url: str = ""
    names: list[str] = []
    logged_in: bool = False
    expires_at: float | None = None


class CookieJarSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sites: list[SiteCookies] = []
    other: int = 0

    cookies: int
    domains: list[str]
    instagram_login: bool
    instagram_expires_at: float | None = None


class SavedCookies(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_name: str
    uploaded_at: float
    summary: CookieJarSummary


class StoredCookies(BaseModel):
    cookies: str
    file_name: str
    uploaded_at: float
