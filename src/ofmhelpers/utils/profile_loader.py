from __future__ import annotations

import re
from abc import ABC, abstractmethod


class PlatformNormalizer(ABC):
    """
    Normalizes a raw profile reference (URL, @handle, or bare username) down
    to a bare username for a single platform.
    """

    #: Hosts that identify this platform, e.g. {"tiktok.com", "www.tiktok.com"}
    HOSTS: frozenset[str] = frozenset()

    #: Matches "<host>/<path>" and captures host + first path segment.
    _URL_PATTERN = re.compile(
        r"(?:https?://)?(?:www\.)?([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})/([^/?#\s]+)"
    )

    def matches(self, raw: str) -> bool:
        """True if `raw` looks like a URL belonging to this platform."""

        match = self._URL_PATTERN.search(raw)
        if not match:
            return False

        host = match.group(1).lower()
        return host in self.HOSTS or any(host.endswith(f".{h}") for h in self.HOSTS)

    def extract_from_url(self, raw: str) -> str:
        """Extract the username from a URL for this platform."""

        match = self._URL_PATTERN.search(raw)
        if not match:
            raise ValueError(f"Could not extract username from URL: {raw!r}")

        return self._clean(match.group(2))

    @staticmethod
    def _clean(username: str) -> str:
        return username.rstrip("/").lstrip("@")

    @abstractmethod
    def normalize(self, raw: str) -> str:
        """Normalize any supported reference (URL, @handle, bare name)."""


class InstagramNormalizer(PlatformNormalizer):
    HOSTS = frozenset({"instagram.com"})

    def normalize(self, raw: str) -> str:
        if self.matches(raw):
            path = self.extract_from_url(raw)
            # instagram.com/<username>/ — but reject non-profile paths.
            if path.lower() in {"p", "reel", "reels", "stories", "tv"}:
                raise ValueError(f"Not a profile URL: {raw!r}")
            return path

        return self._clean(raw.strip())


class TikTokNormalizer(PlatformNormalizer):
    HOSTS = frozenset({"tiktok.com"})

    def normalize(self, raw: str) -> str:
        if self.matches(raw):
            path = self.extract_from_url(raw)
            # tiktok.com/@<username>/video/... — first segment is "@username".
            return self._clean(path)

        return self._clean(raw.strip())


class XNormalizer(PlatformNormalizer):
    """Handles both x.com and the legacy twitter.com domain."""

    HOSTS = frozenset({"x.com", "twitter.com"})

    # Paths on x.com/twitter.com that are never usernames.
    _RESERVED_PATHS = frozenset(
        {
            "i",
            "home",
            "search",
            "explore",
            "notifications",
            "messages",
            "settings",
            "compose",
            "intent",
        }
    )

    def normalize(self, raw: str) -> str:
        if self.matches(raw):
            path = self.extract_from_url(raw)
            if path.lower() in self._RESERVED_PATHS:
                raise ValueError(f"Not a profile URL: {raw!r}")
            return path

        return self._clean(raw.strip())


class ThreadsNormalizer(PlatformNormalizer):
    HOSTS = frozenset({"threads.net"})

    def normalize(self, raw: str) -> str:
        if self.matches(raw):
            # threads.net/@<username>[/post/...]
            path = self.extract_from_url(raw)
            return self._clean(path)

        return self._clean(raw.strip())


class RedditNormalizer(PlatformNormalizer):
    HOSTS = frozenset({"reddit.com"})

    # reddit.com/u/<name> and reddit.com/user/<name> both point at profiles;
    # reddit.com/r/<name> is a subreddit, not a user.
    _USER_PREFIXES = frozenset({"u", "user"})

    _URL_PATTERN = re.compile(
        r"(?:https?://)?(?:www\.|old\.)?(reddit\.com)/(u|user|r)/([^/?#\s]+)"
    )

    def matches(self, raw: str) -> bool:
        return bool(self._URL_PATTERN.search(raw))

    def normalize(self, raw: str) -> str:
        match = self._URL_PATTERN.search(raw)

        if match:
            prefix, name = match.group(2), match.group(3)
            if prefix not in self._USER_PREFIXES:
                raise ValueError(f"Not a user profile URL (subreddit): {raw!r}")
            return self._clean(name)

        return self._clean(raw.strip())


class ProfileNormalizer:
    """
    Normalizes a raw profile reference (URL, @handle, or bare username)
    across multiple platforms by trying each platform's normalizer in turn.
    """

    def __init__(self, normalizers: list[PlatformNormalizer] | None = None):
        self.normalizers = normalizers or [
            InstagramNormalizer(),
            TikTokNormalizer(),
            XNormalizer(),
            ThreadsNormalizer(),
            RedditNormalizer(),
        ]

    def normalize(self, raw: str) -> str:
        raw = raw.strip()

        for normalizer in self.normalizers:
            if normalizer.matches(raw):
                return normalizer.normalize(raw)

        # No platform URL matched — treat as a bare handle/username.
        return raw.lstrip("@").rstrip("/")


class ProfileLoader:
    """Loads a list of usernames from a file, one reference per line."""

    def __init__(self, path: str, normalizer: ProfileNormalizer | None = None):
        self.path = path
        self.normalizer = normalizer or ProfileNormalizer()

    def load(self) -> list[str]:
        with open(self.path) as f:
            return self._normalize(profiles=[line for line in f])

    def _normalize(self, profiles: list[str]) -> list[str]:
        normalized = (
            self.normalizer.normalize(line) for line in profiles if line.strip()
        )
        return list(dict.fromkeys(normalized))


def normalize_profiles_names(profiles: list[str]) -> list[str]:
    normalizer = ProfileNormalizer()
    normalized = (normalizer.normalize(line) for line in profiles if line.strip())
    return list(dict.fromkeys(normalized))
