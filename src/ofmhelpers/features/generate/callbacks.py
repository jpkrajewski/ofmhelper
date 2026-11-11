"""An AI provider calling back about a job's request
(`integrations/ai_providers/self_heal.py`): what finds a task whose submit
answer was lost."""

from collections.abc import Mapping

from pydantic import BaseModel

from ofmhelpers.integrations.ai_providers.self_heal import Heard, heard


class Callback(BaseModel):
    """`known`: a provider of ours. `genuine`: the delivery passed that
    provider's own check."""

    known: bool
    genuine: bool


def receive(
    provider: str, key: str, raw: bytes, headers: Mapping[str, str]
) -> Callback:
    outcome = heard(provider, key, raw, headers)
    return Callback(known=outcome != Heard.UNKNOWN, genuine=outcome != Heard.REFUSED)
