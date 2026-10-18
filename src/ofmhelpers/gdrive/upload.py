"""Uploads to a connected Drive, as that user (their stored refresh token)."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from google.auth.exceptions import GoogleAuthError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.gdrive.oauth import SCOPES, TOKEN_URI
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

_FOLDER_MIME = "application/vnd.google-apps.folder"
_FOLDER_URL = "https://drive.google.com/drive/folders/{}"


def _drive(credentials: Credentials) -> Resource:
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _credentials(refresh_token: str) -> Credentials:
    s = settings.gdrive
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=s.client_id,
        client_secret=s.client_secret,
        scopes=SCOPES,
    )


def account_email(credentials: Credentials) -> str:
    about = _drive(credentials).about().get(fields="user(emailAddress)").execute()
    return about["user"]["emailAddress"]


def _folder_id(drive: Resource, name: str) -> str:
    """The app's folder, created on first use. drive.file sees only what this
    app made, so a same-named folder of the user's own is never picked up."""
    quoted = name.replace("\\", "\\\\").replace("'", "\\'")
    query = f"name = '{quoted}' and mimeType = '{_FOLDER_MIME}' and trashed = false"
    retries = settings.gdrive.upload_retries
    found = (
        drive.files()
        .list(q=query, fields="files(id)", pageSize=1)
        .execute(num_retries=retries)
    )
    if found["files"]:
        return found["files"][0]["id"]
    body = {"name": name, "mimeType": _FOLDER_MIME}
    created = drive.files().create(body=body, fields="id")
    return created.execute(num_retries=retries)["id"]


class UploadReport(BaseModel):
    """One `upload_files` call: what went up, what was there already (a
    retry skips it, so a retry never duplicates), and what failed."""

    folder_url: str
    uploaded: list[str] = []
    already_there: list[str] = []
    failed: list[str] = []


def _names_in(drive: Resource, folder: str) -> set[str]:
    names: set[str] = set()
    page: str | None = None
    while True:
        found = (
            drive.files()
            .list(
                q=f"'{folder}' in parents and trashed = false",
                fields="nextPageToken, files(name)",
                pageSize=1000,
                pageToken=page,
            )
            .execute(num_retries=settings.gdrive.upload_retries)
        )
        names.update(f["name"] for f in found["files"])
        page = found.get("nextPageToken")
        if not page:
            return names


def _upload_one(credentials: Credentials, folder: str, path: Path) -> bool:
    """True once uploaded. A failure is logged and reported, not raised, so
    one bad file never stops the rest."""
    # A service per upload: googleapiclient's HTTP object is not thread-safe.
    media = MediaFileUpload(str(path), resumable=True)
    body = {"name": path.name, "parents": [folder]}
    request = (
        _drive(credentials).files().create(body=body, media_body=media, fields="id")
    )
    try:
        # num_retries: Google's own backoff on 5xx / 429 / dropped
        # connections, resuming the upload where it stopped.
        request.execute(num_retries=settings.gdrive.upload_retries)
    except (HttpError, GoogleAuthError, OSError):
        logger.warning("drive upload failed: %s", path, exc_info=True)
        return False
    return True


def upload_files(refresh_token: str, paths: list[Path]) -> UploadReport:
    """Uploads `paths` side by side (resumable, in chunks) into the app's
    folder, skipping names already in it."""
    credentials = _credentials(refresh_token)
    drive = _drive(credentials)
    folder = _folder_id(drive, settings.gdrive.folder_name)
    there = _names_in(drive, folder)
    report = UploadReport(folder_url=_FOLDER_URL.format(folder))
    report.already_there = [p.name for p in paths if p.name in there]
    todo = [p for p in paths if p.name not in there]
    with ThreadPoolExecutor(settings.gdrive.upload_workers) as pool:
        done = list(pool.map(lambda p: _upload_one(credentials, folder, p), todo))
    for path, ok in zip(todo, done, strict=True):
        (report.uploaded if ok else report.failed).append(path.name)
    return report
