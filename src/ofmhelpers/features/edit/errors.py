"""/edit's refusal, worded for the page (web/exceptions.py: 400)."""


class EditRefusedError(Exception):
    """Nothing was opened or cut; the message says why (shown on the page)."""
