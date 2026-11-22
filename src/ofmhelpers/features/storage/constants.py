import datetime
from functools import lru_cache

from pydantic import BaseModel


class StorageConstants(BaseModel, frozen=True):
    scan_task: str = "storage_scan"
    bin_task: str = "storage_bin"
    bin_url: str = "https://drive.google.com/drive/trash"
    no_drive: str = "Connect Google Drive first"
    connect_first: str = "Connect Google Drive on your Account page first"
    sheet_no_drive: str = "Connect Google Drive on your Account page"
    sheet_failed: str = "Google Drive did not answer. Try again."
    busy: str = "Already moving files to the bin"
    try_again: str = "Could not start. Try again in a moment."
    no_folder: str = "No such account folder"
    read_only: str = "This server only reads the live Drive: moving to the bin is off"
    sync_task: str = "drive_sync"
    root_name: str = "ALL ACCOUNTS"
    dev_prefix: str = "DEV - "
    drive_full: str = "Google Drive is full"
    folder_missing: str = "Drive folder missing"
    no_answer: str = "Google Drive did not answer"
    buy_space_url: str = "https://one.google.com/storage"
    my_drive_url: str = "https://drive.google.com/drive/my-drive"
    bytes_per_mb: int = 1_000_000
    bytes_per_gb: int = 1_000_000_000
    upload_task: str = "drive_upload"
    spoofed_task: str = "repurpose"
    upload_platform: str = "instagram"
    day: datetime.timedelta = datetime.timedelta(days=1)


@lru_cache(maxsize=1)
def storage_constants() -> StorageConstants:
    return StorageConstants()
