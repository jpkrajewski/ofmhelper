"""What a result is called once it is downloaded.

Images and videos are saved camera-roll style -- a prefix plus digits
(IMG_4821.png, VID_0193.mp4) -- instead of the generator's own name. Anything
else (a zip, audio) keeps its name.

The digits come from the file's own name, not a fresh random draw: they look
random from one file to the next, but one file always renders the same, so
a polled gallery fragment is byte-identical while nothing changed and its
ETag answers 304 (templates_config.render_polled).
"""

import hashlib
from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.web.media_kinds import classify_kind


def download_name(name: str) -> str:
    s = settings.web
    prefix = {
        "image": s.image_download_prefix,
        "video": s.video_download_prefix,
    }.get(classify_kind(name))
    if prefix is None:
        return name
    digits = s.download_name_digits
    number = int(hashlib.sha256(name.encode()).hexdigest(), 16) % 10**digits
    return f"{prefix}{number:0{digits}d}{Path(name).suffix}"
