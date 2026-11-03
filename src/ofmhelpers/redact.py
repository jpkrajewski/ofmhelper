"""Masks credentials inside free text: a span's URL, a log message, a stored
error. One place, so a new key shape is one entry here.

Only shapes that cannot be anything else: a bare hex string is left alone (file
names here are content hashes, job ids and trace ids are hex too), so a key with
no prefix of its own is only caught by the name of the URL parameter holding it.
"""

import re

MASK = "[REDACTED]"

# Provider key shapes.
_KEY_SHAPES = (
    # Higgsfield: KEY_ID:KEY_SECRET.
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]{32,}",
    r"ek_live_[A-Za-z0-9_-]{8,}",  # Elyum
    r"gsk_[A-Za-z0-9]{16,}",  # Groq
    r"csk-[A-Za-z0-9]{16,}",  # Cerebras
    r"AIza[0-9A-Za-z_-]{30,}",  # Google / Gemini
    r"apify_api_[A-Za-z0-9]{16,}",  # Apify
    r"Bearer\s+[A-Za-z0-9._~+/-]{16,}=*",
)
KEY_PATTERN = "|".join(_KEY_SHAPES)

# URL parameters whose value is a credential: signed links, OAuth, tokens.
_PARAM_NAMES = (
    r"access_token|api[_-]?key|apikey|auth|code|credential|key|password|refresh_token"
    r"|sessionid|sig|signature|state|token"
    r"|X-Amz-[A-Za-z-]+|X-Goog-[A-Za-z-]+"
)

_KEYS = re.compile(KEY_PATTERN)
_PARAMS = re.compile(rf"(?i)([?&](?:{_PARAM_NAMES})=)[^&\s'\"]+")


def redact(text: str) -> str:
    """`text` with every credential it holds masked."""
    return _PARAMS.sub(rf"\g<1>{MASK}", _KEYS.sub(MASK, text))
