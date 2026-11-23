from functools import lru_cache

from pydantic import BaseModel


class DropboxConstants(BaseModel, frozen=True):
    reconnect: str = "Reconnect Dropbox on your Account page"
    not_set_up: str = "Dropbox is not set up (DROPBOX_APP_KEY, DROPBOX_APP_SECRET)"
    no_sheets: str = (
        "Dropbox has no Google Sheets: put content_links.xlsx in the folder"
    )
    bin_url: str = "https://www.dropbox.com/deleted_files"
    my_drive_url: str = "https://www.dropbox.com/home"
    buy_space_url: str = "https://www.dropbox.com/plans"
    no_drive: str = "Connect Dropbox first"
    connect_first: str = "Connect Dropbox on your Account page first"
    sheet_no_drive: str = "Connect Dropbox on your Account page"
    sheet_failed: str = "Dropbox did not answer. Try again."
    drive_full: str = "Dropbox is full"
    folder_missing: str = "Dropbox folder missing"
    no_answer: str = "Dropbox did not answer"
    va_no_folder: str = (
        "That VA has no Dropbox folder you can upload into: Sync on {page}"
    )
    account_no_folder: str = (
        "That account has no Dropbox folder you can upload into: Sync on {page}"
    )
    remove_failed: str = "Dropbox did not remove it. Try again."
    owner_disconnected: str = "The folder owner's Dropbox was disconnected"
    upload_folder_gone: str = "The Dropbox folder is gone: Sync on {page}"
    connect_once: str = (
        "Your phones' folders live in your own Dropbox ({root})."
        " Connect it once and they are made for you."
    )
    drive_unreadable: str = "Dropbox could not be read"
    folder_unreadable: str = "Could not read this folder from Dropbox"
    owner_not_connected: str = (
        "{owner} has not connected Dropbox: their Account page, Connect Dropbox"
    )
    sync_not_queued: str = "Could not queue the Dropbox sync"
    label: str = "Dropbox"
    root_alias: str = "root"
    ref_prefixes: tuple[str, ...] = ("id:", "/")
    authorize_url: str = "https://www.dropbox.com/oauth2/authorize"
    token_url: str = "https://api.dropboxapi.com/oauth2/token"  # noqa: S105
    not_found_marker: str = "not_found"
    no_space_marker: str = "insufficient_space"
    conflict_marker: str = "conflict"
    xlsx_suffix: str = ".xlsx"
    image_suffixes: tuple[str, ...] = (
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".tif",
        ".tiff",
        ".webp",
    )
    upload_chunk_bytes: int = 8 * 1024 * 1024


@lru_cache(maxsize=1)
def dropbox_constants() -> DropboxConstants:
    return DropboxConstants()
