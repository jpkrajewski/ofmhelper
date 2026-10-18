"""Uploads to a connected Drive, as that user (their stored refresh token)."""

from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build
from googleapiclient.http import MediaFileUpload

from ofmhelpers.config import settings
from ofmhelpers.gdrive.oauth import SCOPES, TOKEN_URI

_FOLDER_MIME = "application/vnd.google-apps.folder"


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
    found = drive.files().list(q=query, fields="files(id)", pageSize=1).execute()
    if found["files"]:
        return found["files"][0]["id"]
    body = {"name": name, "mimeType": _FOLDER_MIME}
    return drive.files().create(body=body, fields="id").execute()["id"]


def upload_file(refresh_token: str, path: Path) -> str:
    """Uploads `path` (resumable, in chunks) into the app's folder and
    returns the file's Drive link."""
    drive = _drive(_credentials(refresh_token))
    folder = _folder_id(drive, settings.gdrive.folder_name)
    media = MediaFileUpload(str(path), resumable=True)
    body = {"name": path.name, "parents": [folder]}
    created = (
        drive.files()
        .create(body=body, media_body=media, fields="webViewLink")
        .execute()
    )
    return created["webViewLink"]
