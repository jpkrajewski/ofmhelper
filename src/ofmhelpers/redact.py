import re

MASK = "[REDACTED]"

_KEY_SHAPES = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]{32,}",
    r"ek_live_[A-Za-z0-9_-]{8,}",
    r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9]{32,}",
    r"gsk_[A-Za-z0-9]{16,}",
    r"csk-[A-Za-z0-9]{16,}",
    r"AIza[0-9A-Za-z_-]{30,}",
    r"apify_api_[A-Za-z0-9]{16,}",
    r"(?<!\d)\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])",
    r"Bearer\s+[A-Za-z0-9._~+/-]{16,}=*",
)
KEY_PATTERN = "|".join(_KEY_SHAPES)

_PARAM_NAMES = (
    r"access_token|api[_-]?key|apikey|auth|code|credential|key|password|refresh_token"
    r"|sessionid|sig|signature|state|token"
    r"|X-Amz-[A-Za-z-]+|X-Goog-[A-Za-z-]+"
)

_PATH_TOKENS = re.compile(r"(/password/|/files/ai/)[^/?#\s'\"]+")

_KEYS = re.compile(KEY_PATTERN)
_PARAMS = re.compile(rf"(?i)([?&](?:{_PARAM_NAMES})=)[^&\s'\"]+")


def redact(text: str) -> str:
    return _PARAMS.sub(rf"\g<1>{MASK}", path_tokens(_KEYS.sub(MASK, text)))


def path_tokens(text: str) -> str:
    return _PATH_TOKENS.sub(rf"\g<1>{MASK}", text)
