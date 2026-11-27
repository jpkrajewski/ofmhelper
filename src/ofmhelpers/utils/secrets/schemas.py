# Copyright (C) 2026 jpkrajewski

from typing import Annotated

from pydantic import (
    BaseModel,
    SecretStr,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    WrapSerializer,
)


class Revealed(BaseModel, frozen=True):
    reason: str


def _masked_unless_revealed(
    value: SecretStr,
    handler: SerializerFunctionWrapHandler,
    info: SerializationInfo,
) -> str | SecretStr:
    if isinstance(info.context, Revealed):
        return value.get_secret_value()
    masked: str | SecretStr = handler(value)
    return masked


Secret = Annotated[SecretStr, WrapSerializer(_masked_unless_revealed)]
