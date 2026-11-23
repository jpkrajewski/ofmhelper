from pydantic import BaseModel, ConfigDict


class DropboxGrant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    refresh_token: str
    account_id: str


class DropboxAccount(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: str
    email: str
    name: str
