"""What kind of media a file is, by its extension.

Its own module, importing nothing of the app: the upload helpers, the
previews (`web/previews.py`, which the job store calls) and the routers all
need it, and none of them may pull in the others.
"""

from pathlib import Path

# Keep in step with utils/metadata_cleaner.SUPPORTED_EXTENSIONS: anything the
# cleaner accepts must classify as an image here, or require_upload_kind
# rejects an upload the tool behind it can actually process.
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}


VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}


AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg"}


def classify_kind(name: str) -> str:
    """image / video / audio / other, by file extension."""
    ext = Path(name).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return "other"
