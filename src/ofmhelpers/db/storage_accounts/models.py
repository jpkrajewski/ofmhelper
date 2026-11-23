# Copyright (C) 2026 jpkrajewski

from pydantic import BaseModel, ConfigDict

from ofmhelpers.db.users.schema import StorageProvider


class StorageAccount(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: int
    provider: StorageProvider
    account_id: str
    email: str
    connected_at: float
