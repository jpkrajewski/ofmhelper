# Copyright (C) 2026 jpkrajewski

from functools import lru_cache

from pydantic import BaseModel


class StorageClientConstants(BaseModel, frozen=True):
    read_only: str = (
        "read-live mode (OFM_DRIVE_READ_LIVE): writing to the live Drive is off"
    )


@lru_cache(maxsize=1)
def client_constants() -> StorageClientConstants:
    return StorageClientConstants()
