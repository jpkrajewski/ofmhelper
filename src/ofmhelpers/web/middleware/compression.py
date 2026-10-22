"""gzip for what compresses: pages, fragments, JSON, CSS and JS.

The host's nginx gzips text/html only (its default `gzip_types`), so
the CSS (~90 KB), the JS and every polled JSON payload crossed slow
connections uncompressed. Starlette's GZip already skips media (video/*,
audio/*, png/jpeg/webp ...); the policy here adds downloads
(`application/octet-stream`: a 30 MB result re-compressed per request is CPU
for nothing, and Content-Length would be lost) and uses a cheaper level than
Starlette's 9.
"""

from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES, GZipMiddleware
from starlette.types import ASGIApp

EXCLUDED_CONTENT_TYPES = (*DEFAULT_EXCLUDED_CONTENT_TYPES, "application/octet-stream")


class CompressionMiddleware(GZipMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(
            app,
            minimum_size=1024,
            compresslevel=6,
            exclude_content_types=EXCLUDED_CONTENT_TYPES,
        )
