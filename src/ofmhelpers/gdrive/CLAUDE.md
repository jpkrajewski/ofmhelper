# gdrive

Google Drive as a **service account** (`settings.gdrive`), for /drive (web/drive_folders.py). Google's own libraries (`google-api-python-client`, `google-auth`).

- `client.py` — `connect()` -> `Drive` (one per thread: httplib2 is not thread-safe). `folder` (raises `FolderGoneError` for missing / trashed / not shared), `children` (paged, newest first, no trash), `find_folder` (oldest of that name, so a hand-made one is reused), `create_folder`, `ensure_folder`, `rename`, `thumbnail(id, px)` (fetches `thumbnailLink` with the account's token). Every request `execute(num_retries=OFM_GDRIVE_RETRIES)`. Any failure is a `DriveError` with a readable message. `service_account_email()` = who the root must be shared with.
- `models.py` — `DriveFolder`, `DriveFile` (Drive's field names as aliases; `kind` image/video/folder/file, `url`).
- Never deletes or moves anything.

## Setup
1. Google Cloud Console: enable **Google Drive API**; IAM -> Service accounts -> create, Keys -> JSON.
2. Share the Drive folder with the account's `client_email` as **Editor**.
3. `.env`: `OFM_GDRIVE_SERVICE_ACCOUNT='<key JSON, one line>'` (single quotes: compose and dotenv keep it literal), `OFM_GDRIVE_ROOT_FOLDER=<folder id>`.

## Findings
- Service account over OAuth: no consent screen, no 7-day refresh-token expiry of an unpublished OAuth app, and the full `drive` scope (needed: `drive.file` sees only files the app created, never the VA's uploads or an existing folder) is a *restricted* scope that a public OAuth app must pass Google's security assessment for. Sharing one folder with the account limits it to that folder. https://developers.google.com/workspace/drive/api/guides/api-specific-auth
- Service accounts have no storage quota: they can make folders (no bytes) in a user's folder but uploads into My Drive fail ("Service Accounts do not have storage quota"). Folders the bot makes are owned by it; files the VA uploads stay the VA's. A Shared Drive removes both caveats. https://developers.google.com/workspace/drive/api/guides/about-shareddrives
- `thumbnailLink` is short-lived and, for a non-public file, answers only with the caller's token: hence the server-side proxy, not an `<img>` straight to Google. Its `=s220` suffix sets the size. Videos get one once Drive has processed them. https://developers.google.com/workspace/drive/api/reference/rest/v3/files
- A 404 on `files.get` means "not shared with this account" as often as "does not exist": Drive does not tell them apart.
- `build(..., static_discovery)` defaults to the discovery document bundled with the library: no network call to build a service.
