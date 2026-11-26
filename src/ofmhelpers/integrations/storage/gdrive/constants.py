# Copyright (C) 2026 jpkrajewski

from functools import lru_cache

from pydantic import BaseModel


class GDriveConstants(BaseModel, frozen=True):
    sheet_mime: str = "application/vnd.google-apps.spreadsheet"
    folder_url: str = "https://drive.google.com/drive/folders/{}"


@lru_cache(maxsize=1)
def gdrive_constants() -> GDriveConstants:
    return GDriveConstants()
