# Copyright (C) 2026 jpkrajewski

from fastapi.responses import RedirectResponse

NO_STORE = frozendict({"Cache-Control": "no-store"})
PRIVATE_FOR_A_DAY = frozendict({"Cache-Control": "private, max-age=86400"})


def see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=303)
