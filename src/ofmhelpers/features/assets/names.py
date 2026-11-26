# Copyright (C) 2026 jpkrajewski

import hashlib
from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.features.kinds import FileKind


def download_name(name: str, job_id: str = "") -> str:
    s = settings.web
    prefix = {
        FileKind.IMAGE: s.image_download_prefix,
        FileKind.VIDEO: s.video_download_prefix,
    }.get(FileKind.from_ext(name))
    if prefix is None:
        return name
    digits = s.download_name_digits
    source = f"{job_id}/{name}" if job_id else name
    number = int(hashlib.sha256(source.encode()).hexdigest(), 16) % 10**digits
    return f"{prefix}{number:0{digits}d}{Path(name).suffix}"
