"""What a request that crashed answers. Nothing raw reaches the page: the
exception and its traceback are in the logs and the request's trace."""

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ofmhelpers.log import get_logger
from ofmhelpers.web.middleware import AuthMiddleware

logger = get_logger(__name__)

MESSAGE = "Something went wrong. Try again."


async def unhandled(request: Request, exc: Exception) -> Response:
    logger.error("%s %s crashed", request.method, request.url.path, exc_info=exc)
    if AuthMiddleware.is_fetch(request):
        return JSONResponse({"detail": MESSAGE}, status_code=500)
    return HTMLResponse(f"<p>{MESSAGE}</p>", status_code=500)
