class SocialError(Exception): ...


class RetryableError(SocialError):
    def __init__(
        self, message: str, *, reached: bool = True, dead_proxy: bool = False
    ) -> None:
        super().__init__(message)
        self.reached = reached
        self.dead_proxy = dead_proxy


class RateLimitedError(RetryableError): ...
