# Copyright (C) 2026 jpkrajewski

import re
from abc import ABC, abstractmethod
from functools import lru_cache
from types import MappingProxyType
from typing import ClassVar
from urllib.parse import urlsplit

from pydantic import BaseModel

from ofmhelpers.db.tracked_accounts.schema import CreatorName
from ofmhelpers.db.watched_profiles.schema import ProfileHandle


class PlatformNames(ABC):
    key: ClassVar[str]
    label: ClassVar[str]

    @abstractmethod
    def handle(self, text: str) -> str: ...

    @abstractmethod
    def profile_url(self, handle: str) -> str: ...


class InstagramNames(PlatformNames):
    key = "instagram"
    label = "Instagram"

    def handle(self, text: str) -> str:
        return ProfileHandle(username=text).username

    def profile_url(self, handle: str) -> str:
        return f"https://www.instagram.com/{handle}/"


class _HandleRule(BaseModel, frozen=True):
    label: str
    hosts: tuple[str, ...]
    pattern: re.Pattern[str]

    def bare(self, text: str) -> str:
        value = text.strip()
        if any(host in value.lower() for host in self.hosts):
            path = urlsplit(value if "//" in value else f"https://{value}").path
            parts = [p for p in path.split("/") if p and p.lower() != "add"]
            value = parts[0] if parts else ""
        value = value.lstrip("@").strip("/")
        if not self.pattern.fullmatch(value):
            msg = f"Not a valid {self.label} username: {text!r}"
            raise ValueError(msg)
        return value.lower()


_SNAPCHAT = _HandleRule(
    label="Snapchat",
    hosts=("snapchat.com",),
    pattern=re.compile(r"[A-Za-z][A-Za-z0-9._-]{2,14}"),
)
_X = _HandleRule(
    label="X",
    hosts=("x.com", "twitter.com"),
    pattern=re.compile(r"[A-Za-z0-9_]{1,15}"),
)


class SnapchatNames(PlatformNames):
    key = "snapchat"
    label = "Snapchat"

    def handle(self, text: str) -> str:
        return _SNAPCHAT.bare(text)

    def profile_url(self, handle: str) -> str:
        return f"https://www.snapchat.com/@{handle}"


class XNames(PlatformNames):
    key = "x"
    label = "X"

    def handle(self, text: str) -> str:
        return _X.bare(text)

    def profile_url(self, handle: str) -> str:
        return f"https://x.com/{handle}"


_NAMES: tuple[type[PlatformNames], ...] = (InstagramNames, SnapchatNames, XNames)


@lru_cache(maxsize=1)
def platform_names() -> MappingProxyType[str, PlatformNames]:
    return MappingProxyType({cls.key: cls() for cls in _NAMES})


def names_of(key: str) -> PlatformNames:
    return platform_names()[key]


class AddAccounts(BaseModel):
    platform: str
    handles: str
    creator: CreatorName = ""

    def parsed(self) -> list[str]:
        if self.platform not in platform_names():
            msg = f"Unknown platform: {self.platform}"
            raise ValueError(msg)
        platform = names_of(self.platform)
        handles: list[str] = []
        bad: list[str] = []
        for line in self.handles.splitlines():
            words = line.split()
            if not words:
                continue
            try:
                handles.append(platform.handle(words[0]))
            except ValueError:  # silent: a bad line is listed back
                bad.append(line.strip())
        if bad:
            msg = f"Not a valid {platform.label} account: " + ", ".join(bad[:5])
            raise ValueError(msg)
        return list(dict.fromkeys(handles))
