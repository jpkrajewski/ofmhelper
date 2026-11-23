# Copyright (C) 2026 jpkrajewski

import os
import tempfile
from contextlib import contextmanager
from http.cookiejar import LoadError, MozillaCookieJar
from pathlib import Path
lazy from collections.abc import Iterator


class CookieFileError(ValueError): ...


@contextmanager
def cookie_file(cookies: str | None) -> Iterator[str | None]:
    if not cookies:
        yield None
        return
    fd, path = tempfile.mkstemp(prefix="cookies-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(cookies)
        yield path
    finally:
        Path(path).unlink(missing_ok=True)


def load_jar(cookies: str) -> MozillaCookieJar:
    with cookie_file(cookies) as path:
        jar = MozillaCookieJar()
        try:
            jar.load(path, ignore_discard=True, ignore_expires=True)
        except LoadError, OSError, UnicodeDecodeError:
            msg = (
                "Not a cookies.txt file: export one in Netscape format "
                '(it starts with "# Netscape HTTP Cookie File")'
            )
            raise CookieFileError(msg) from None
    return jar
