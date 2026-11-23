# Copyright (C) 2026 jpkrajewski

import datetime
from enum import StrEnum
from functools import lru_cache

from pydantic import BaseModel


class StorageTask(StrEnum):
    SCAN = "storage_scan"
    BIN = "storage_bin"
    SYNC = "drive_sync"
    UPLOAD = "drive_upload"
    SPOOFED = "repurpose"


class PostingConstants(BaseModel, frozen=True):
    day: datetime.timedelta = datetime.timedelta(days=1)


class StorageConstants(BaseModel, frozen=True):
    bin_url: str = "https://drive.google.com/drive/trash"
    no_drive: str = "Connect Google Drive first"
    connect_first: str = "Connect Google Drive on your Account page first"
    sheet_no_drive: str = "Connect Google Drive on your Account page"
    sheet_failed: str = "Google Drive did not answer. Try again."
    busy: str = "Already moving files to the bin"
    try_again: str = "Could not start. Try again in a moment."
    no_folder: str = "No such account folder"
    read_only: str = "This server only reads the live Drive: moving to the bin is off"
    root_name: str = "ALL ACCOUNTS"
    dev_prefix: str = "DEV - "
    drive_full: str = "Google Drive is full"
    folder_missing: str = "Drive folder missing"
    no_answer: str = "Google Drive did not answer"
    buy_space_url: str = "https://one.google.com/storage"
    my_drive_url: str = "https://drive.google.com/drive/my-drive"
    bytes_per_mb: int = 1_000_000
    bytes_per_gb: int = 1_000_000_000
    upload_platform: str = "instagram"
    account_page: str = "/account"
    drive_page: str = "/drive"
    upload_not_queued: str = "Could not queue the upload"
    upload_not_queued_why: str = "Could not queue the upload: {reason}"
    va_no_folder: str = (
        "That VA has no Drive folder you can upload into: Sync on {page}"
    )
    pick_folder: str = "Pick a folder"
    account_no_folder: str = (
        "That account has no Drive folder you can upload into: Sync on {page}"
    )
    no_such_upload: str = "No such upload"
    only_done_removable: str = "Only an uploaded video can be removed"
    remove_failed: str = "Google Drive did not remove it. Try again."
    file_missing: str = "The file is no longer on the server"
    owner_disconnected: str = "The folder owner's Google Drive was disconnected"
    folder_outside_root: str = (
        "The account's folder is not in its owner's {root} folder:"
        " Sync on {page}, then upload again"
    )
    upload_folder_gone: str = "The Drive folder is gone: Sync on {page}"
    upload_failed: str = "Upload failed: {reason}"
    no_preview: str = "No preview"
    no_preview_yet: str = "No preview yet"
    not_set_up: str = (
        "Google Drive works through each person's Google sign-in, which is"
        " not set up: OFM_GOOGLE_CLIENT_ID and OFM_GOOGLE_CLIENT_SECRET are"
        " empty in .env."
    )
    connect_once: str = (
        "Your phones' folders live in your own Google Drive ({root})."
        " Connect it once and they are made for you."
    )
    bot_root_unusable: str = (
        "The Drive bot cannot use OFM_GDRIVE_ROOT_FOLDER: share it with {email}{named}"
    )
    bot_root_name_it: str = " and name it {root}"
    account_folder_deleted: str = (
        "The account's folder was deleted: the account was removed"
    )
    drive_unreadable: str = "Drive could not be read"
    folder_unreadable: str = "Could not read this folder from Drive"
    owner_not_connected: str = (
        "{owner} has not connected Google Drive:"
        " their Account page, Connect Google Drive"
    )
    unknown_owner: str = "user {user_id}"
    sync_not_queued: str = "Could not queue the Drive sync"
    root_mismatch: str = "Root {root_id} is {found!r}, not {expected!r}"


@lru_cache(maxsize=1)
def posting_constants() -> PostingConstants:
    return PostingConstants()
