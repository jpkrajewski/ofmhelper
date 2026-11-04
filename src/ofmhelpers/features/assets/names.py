"""What a result is called once it is downloaded.

Images and videos are saved camera-roll style -- a prefix plus digits
(IMG_4821.png, VID_0193.mp4) -- instead of the generator's own name. Anything
else (a zip, audio) keeps its name.

The digits come from the file's job and name, not a fresh random draw: they
look random from one file to the next, but one file always renders the same, so
a polled gallery fragment is byte-identical while nothing changed and its
ETag answers 304 (templates_config.render_polled). The job counts because names
repeat between jobs: every /clean-images run's first image is IMG_0001.png, and
from the name alone all of them saved as "IMG_3133 (9).png" (2026-11-03).
"""

import hashlib
from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.features.assets.kinds import classify_kind


def download_name(name: str, job_id: str = "") -> str:
    """`job_id`: the job the file is a result of, when it is served as one."""
    s = settings.web
    prefix = {
        "image": s.image_download_prefix,
        "video": s.video_download_prefix,
    }.get(classify_kind(name))
    if prefix is None:
        return name
    digits = s.download_name_digits
    source = f"{job_id}/{name}" if job_id else name
    number = int(hashlib.sha256(source.encode()).hexdigest(), 16) % 10**digits
    return f"{prefix}{number:0{digits}d}{Path(name).suffix}"
