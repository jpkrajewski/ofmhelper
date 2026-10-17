"""What a result is called once it is downloaded.

Images and videos are saved camera-roll style -- a prefix plus random digits
(IMG_4821.png, VID_0193.mp4) -- instead of the generator's own name. Anything
else (a zip, audio) keeps its name.
"""

import secrets
from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.web.routers.task_helpers.uploads import classify_kind


def download_name(name: str) -> str:
    s = settings.web
    prefix = {
        "image": s.image_download_prefix,
        "video": s.video_download_prefix,
    }.get(classify_kind(name))
    if prefix is None:
        return name
    digits = s.download_name_digits
    number = secrets.randbelow(10**digits)
    return f"{prefix}{number:0{digits}d}{Path(name).suffix}"
