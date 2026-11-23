# Copyright (C) 2026 jpkrajewski

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ofmhelpers.features.generate import hosted as hosting

router = APIRouter(prefix=hosting.PREFIX, tags=["ai-files"])


@router.api_route("/{token}/{name}", methods=["GET", "HEAD"])
def hosted(token: str, name: str) -> FileResponse:  # noqa: ARG001
    found = hosting.hosted(token)
    return FileResponse(
        found.path,
        media_type=found.media_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
