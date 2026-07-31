"""Cross-cutting request handling, one concern per file.

Everything here runs on *every* request, before routing. `auth.py` owns the
whole auth story (the allowlist check, the password check, `require_admin`) on
`AuthMiddleware` itself; `ratelimit.py` is only the per-request wiring over
`web/ratelimit.py`'s counters.

Order matters and is decided in `web/main.py`, not here: Starlette applies
middleware outside-in in *added* order, so the last `add_middleware` call ends
up outermost. A request passes SessionMiddleware (reads/signs the cookie) ->
WriteRateLimitMiddleware (drops a flood before any auth work) -> AuthMiddleware
(gates everything not on the public allowlist).
"""

from ofmhelpers.web.middleware.auth import AuthMiddleware
from ofmhelpers.web.middleware.ratelimit import WriteRateLimitMiddleware

__all__ = ["AuthMiddleware", "WriteRateLimitMiddleware"]
