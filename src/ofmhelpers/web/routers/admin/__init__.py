"""
Admin-only surfaces. Every router here is gated at the router level with
`dependencies=[Depends(AuthMiddleware.require_admin)]` (see
web/middleware/auth.py) rather than
per-route, so a new endpoint in these files is admin-only by default: the
landing-page applications, the file manager, the action log, and the yt-dlp
cookie upload.
"""
