"""
Admin-only surfaces. Every router here is gated at the router level with
`dependencies=[AdminOnly]` (see web/dependencies.py) rather than
per-route, so a new endpoint in these files is admin-only by default: who
may sign in (/users), the landing-page applications, the file manager, the
action log and the styleguide.
"""
