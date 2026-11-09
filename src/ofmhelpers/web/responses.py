"""The answers many routes share: on to another page, and cache headers."""

from fastapi.responses import RedirectResponse

# A page or file holding something private: never kept anywhere.
NO_STORE = frozendict({"Cache-Control": "no-store"})
# Something made once and kept on disk (a cover, a thumbnail).
PRIVATE_FOR_A_DAY = frozendict({"Cache-Control": "private, max-age=86400"})


def see_other(url: str) -> RedirectResponse:
    """After a form's POST: on to `url`, as a GET."""
    return RedirectResponse(url=url, status_code=303)
