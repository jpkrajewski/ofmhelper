import os
from pathlib import Path

COOKIES_FILE = Path(os.getenv("OFM_COOKIES_FILE", "cookies/cookies.txt"))


def get_cookiefile() -> str | None:
    return str(COOKIES_FILE) if COOKIES_FILE.is_file() else None
