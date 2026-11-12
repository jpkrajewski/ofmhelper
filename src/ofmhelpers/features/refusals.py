"""What a feature raises to refuse a request, worded for the person: the
message is the page's `{"detail": ...}` (`web/exceptions.py` `REFUSALS` gives
each its status). A feature never knows HTTP; a route never catches these."""

lazy from pydantic import BaseModel


class RefusedError(ValueError):
    """The request cannot be done as asked (400). `detail`: what the page
    gets in place of the sentence, when its script reads more than one
    (/clone-links' refused paste: every bad line)."""

    def __init__(self, message: str, detail: BaseModel | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class ForbiddenError(PermissionError):
    """Not the asker's to see or change (403)."""


class NotFoundError(LookupError):
    """What was asked for is not there (404)."""


class ConflictError(ValueError):
    """What is there already stands in the way (409)."""


class TooSoonError(RuntimeError):
    """Asked again before its rest was over (429)."""


class UnavailableError(RuntimeError):
    """An outside service did not do its part (502). The message is ours;
    the service's own error goes to the log, never to the page."""


class TryAgainError(RuntimeError):
    """Not done this time, the same request may work next time (503)."""
