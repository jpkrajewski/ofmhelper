"""Pre-fill values for the provider API-key fields the generation forms show.

Not auth: these decide what a form field starts out containing, not who may
reach it. They only read the session because *which* kie.ai key to pre-fill is
a per-role choice.

Optional by design -- an unset var just means the field starts empty and the
user pastes a key in manually, same as before roles existed.
"""

from starlette.requests import Request

from ofmhelpers.config import settings


def get_kie_api_key(request: Request) -> str:
    """Pre-fill value for the kie.ai API key field, based on the logged-in role."""
    s = settings.web
    role = request.session.get("role")
    key = s.kie_ai_api_key_admin if role == s.role_admin else s.kie_ai_api_key_va
    return key or ""


def get_elevenlabs_api_key() -> str:
    """Pre-fill value for the ElevenLabs API key field.

    Same optional-by-design contract as get_kie_api_key, but role-blind:
    `ELEVENLABS_API_KEY` is one workspace key, so admin and VA get the same
    one, and an unset var just leaves the field empty.
    """
    return settings.web.elevenlabs_api_key or ""
