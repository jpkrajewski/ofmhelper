from pathlib import Path

from ofmhelpers.config import settings

COOKIES_FILE = Path(settings.downloaders.cookies_file)


def get_cookiefile() -> str | None:
    return str(COOKIES_FILE) if COOKIES_FILE.is_file() else None
