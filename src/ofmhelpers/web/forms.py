from typing import Annotated, Any

from fastapi import UploadFile
from pydantic import BaseModel, Field, StringConstraints, model_validator

from ofmhelpers.config import settings

Stripped = Annotated[str, StringConstraints(strip_whitespace=True)]
Upper = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True)]
Files = Annotated[list[UploadFile], Field(default_factory=list)]


def at_most[N: (int, float)](value: N, limit: N) -> N:
    if value > limit:
        msg = f"Input should be less than or equal to {limit}"
        raise ValueError(msg)
    return value


class LoggedFields(BaseModel): ...


class FormModel(LoggedFields):
    @model_validator(mode="before")
    @classmethod
    def _blank_is_not_sent(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return {name: value for name, value in data.items() if value != ""}


class PaidForm(FormModel):
    api_key: Stripped = ""
    provider: str = Field(default_factory=lambda: settings.aigen.default_provider)
