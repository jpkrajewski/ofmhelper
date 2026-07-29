"""
Admin-only surfaces. Every router here is gated at the router level with
`dependencies=[Depends(require_admin)]` (see web/auth.py) rather than
per-route, so a new endpoint in these files is admin-only by default: the
model roster, the competition board, the file manager, the action log, and
the yt-dlp cookie upload.
"""
