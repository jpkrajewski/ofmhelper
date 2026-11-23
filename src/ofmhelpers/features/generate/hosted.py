# Copyright (C) 2026 jpkrajewski

import mimetypes
from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.features.refusals import NotFoundError
from ofmhelpers.integrations.ai_providers import hosting

PREFIX = hosting.HOSTED_PATH


class HostedFile(BaseModel):
    path: Path
    media_type: str


def hosted(token: str) -> HostedFile:
    path = hosting.hosted_file(token)
    if path is None:
        msg = "Not Found"
        raise NotFoundError(msg)
    return HostedFile(
        path=path,
        media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
    )
