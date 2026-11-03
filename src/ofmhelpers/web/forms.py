"""What a browser's form sends, as one typed object a route takes whole:
`form: Annotated[SomeForm, Form()]`.

Two things to know, both measured on FastAPI 0.143 (2026-11-03):
- A form's files go INSIDE the model (`image: Files`). A model next to a
  separate `File()` parameter is answered 422 "body.form: Field required",
  whatever is sent.
- A form model keeps a blank field as "", where a `Form()` parameter takes its
  default. A browser sends every input, filled or not, so `FormModel` drops the
  blank ones first and a default means the same in both. Only exactly "": spaces
  are a value, refused or kept like any other (a paid form's `batch` of spaces
  must stay a 422, not become "no batch" and lose its pay-twice guard).

Normalising a value belongs on its field (`Stripped`, `Upper`). A refusal the
page has to word stays in the route or service: a validator's failure answers
422, which the pages do not show as a sentence.

Telemetry reads a `LoggedFields` model (every form, `GalleryQuery`) field by
field (`telemetry._request_attributes`), through the same allow-list as any
argument, so `api_key` stays hidden and a new field stays out of Logfire until
it is listed there.
"""

from typing import Annotated, Any

from fastapi import UploadFile
from pydantic import BaseModel, Field, StringConstraints, model_validator

from ofmhelpers.config import settings

Stripped = Annotated[str, StringConstraints(strip_whitespace=True)]
Upper = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True)]
Files = Annotated[list[UploadFile], Field(default_factory=list)]


class LoggedFields(BaseModel):
    """A route's arguments taken as one object: telemetry reads them one by one."""


class FormModel(LoggedFields):
    """A browser's form: a blank field counts as not sent."""

    @model_validator(mode="before")
    @classmethod
    def _blank_is_not_sent(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return {name: value for name, value in data.items() if value != ""}


class PaidForm(FormModel):
    """What every form that spends a provider's credits starts with."""

    api_key: Stripped = ""
    provider: str = Field(default_factory=lambda: settings.aigen.default_provider)
