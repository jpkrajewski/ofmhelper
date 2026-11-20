from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES, GZipMiddleware
lazy from starlette.types import ASGIApp

EXCLUDED_CONTENT_TYPES = (*DEFAULT_EXCLUDED_CONTENT_TYPES, "application/octet-stream")


class CompressionMiddleware(GZipMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(
            app,
            minimum_size=1024,
            compresslevel=6,
            exclude_content_types=EXCLUDED_CONTENT_TYPES,
        )
