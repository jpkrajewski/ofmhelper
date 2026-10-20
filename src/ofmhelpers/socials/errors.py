"""The only failures a platform's `fetch` raises, so the tracker that rotates
proxies never needs to know which platform it is scanning. A platform maps
its own client's exceptions onto these."""


class SocialError(Exception):
    """A lasting answer worth showing on the account as it is (the platform
    changed its API, the account is unreadable). Retrying elsewhere would
    get the same."""


class RetryableError(SocialError):
    """Worth another try through another connection.

    `reached`: the platform itself answered (a 5xx, "not found" -- which a
    flagged proxy IP also gets). False when the connection never got there:
    then it is not the account's fault, and nothing is recorded.
    `dead_proxy`: the proxy itself is broken (refused, TLS, connect timeout),
    so it leaves the job's pool."""

    def __init__(
        self, message: str, *, reached: bool = True, dead_proxy: bool = False
    ) -> None:
        super().__init__(message)
        self.reached = reached
        self.dead_proxy = dead_proxy


class RateLimitedError(RetryableError):
    """The platform is refusing this IP for now. Only when every try of an
    account was rate-limited does a batch stop."""
