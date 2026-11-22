from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


def drawing_url(model_id: str | None, color_id: str | None) -> str:
    return f"/phones/drawing.svg?model={model_id or ''}&color={color_id or ''}"


class AssignAccount(BaseModel):
    account_id: int
    replace: bool = False


class LoginSecrets(BaseModel):
    password: str
    backup_codes: str


class AddedLogin(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    existed: bool


class ArchivedLogin(BaseModel):
    id: int
    service: str
    username: str
    archived_at: float = 0
    on: list[str] = Field(default_factory=list)


_THOUSAND = 1_000
_MILLION = 1_000_000


def _compact(n: int) -> str:
    if n < _THOUSAND:
        return str(n)
    thousands = round(n / _THOUSAND, 1)
    if thousands < _THOUSAND:
        return f"{thousands:.1f}".rstrip("0").rstrip(".") + "k"
    return f"{round(n / _MILLION, 1):.1f}".rstrip("0").rstrip(".") + "M"


def followers_text(n: int | None) -> str:
    if n is None:
        return ""
    return f"{_compact(n)} follower{'' if n == 1 else 's'}"


class PhoneSort(StrEnum):
    FOLLOWERS = "followers"
    LABEL = "label"
    MODEL = "model"
    UPDATED = "updated"
    LOGINS = "logins"
