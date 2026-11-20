lazy from collections.abc import Mapping

from pydantic import BaseModel

from ofmhelpers.integrations.ai_providers.self_heal import Heard, heard


class Callback(BaseModel):
    known: bool
    genuine: bool


def receive(
    provider: str, key: str, raw: bytes, headers: Mapping[str, str]
) -> Callback:
    outcome = heard(provider, key, raw, headers)
    return Callback(known=outcome != Heard.UNKNOWN, genuine=outcome != Heard.REFUSED)
