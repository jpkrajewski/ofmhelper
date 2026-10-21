# gdrive

Google Drive as a **service account** (`settings.gdrive`), for /drive (web/drive_folders.py). Google's own libraries (`google-api-python-client`, `google-auth`).

- `base.py` — `DriveApi` (Protocol every Drive implements; callers type against it), `DriveError`, `FolderGoneError`.
- `client.py` — `connect()` -> `Drive` (one per thread: httplib2 is not thread-safe); `FakeDrive` under `OFM_FAKE_EXTERNALS`. `folder` (raises `FolderGoneError` for missing / trashed / not shared), `children` (paged, newest first, no trash), `find_folder` (oldest of that name, so a hand-made one is reused), `create_folder`, `ensure_folder`, `rename`, `file(id)` (with `parents`, `thumbnailLink`, `ownedByMe`; trashed = `FolderGoneError`), `thumbnail(file, px)` (fetches its `thumbnailLink` with the account's token). Every request `execute(num_retries=OFM_GDRIVE_RETRIES)`. Any failure is a `DriveError` with a readable message. `service_account_email()` = who the root must be shared with.
- `models.py` — `DriveFolder`, `DriveFile` (Drive's field names as aliases; `kind` image/video/folder/file, `url`).
- `fake.py` — `FakeDrive`: same interface, real semantics (non-unique names, parents, trash != delete, visibility = shared item or a folder above it, owner per item, previews only where there are). Memory, or `OFM_FAKES_DIR/drive.json` under `fcntl` lock (the app under e2e and the test share it). Test hooks: `seed_folder`, `add_file`, `trash`, `delete`, `rename_by_hand`, `share`/`unshare`, `fail_on`, `named`, `calls`/`writes`. Kept honest by `tests/contract/test_drive_contract.py`.
- Never deletes or moves anything.

## Setup
1. Google Cloud Console: enable **Google Drive API**; IAM -> Service accounts -> create, Keys -> JSON.
2. Share the Drive folder with the account's `client_email` as **Editor**.
3. `.env`: `OFM_GDRIVE_SERVICE_ACCOUNT='<key JSON, one line>'` (single quotes: compose and dotenv keep it literal), `OFM_GDRIVE_ROOT_FOLDER=<folder id>`, `OFM_GDRIVE_PRODUCTION_ROOT_FOLDER=<live folder id>` (every .env), `OFM_ENV=production` on the server only.
4. Dev/sandbox: a separate folder shared with the bot as `OFM_GDRIVE_ROOT_FOLDER`; the live one is refused outside production.

## Findings
- Service account over OAuth: no consent screen, no 7-day refresh-token expiry of an unpublished OAuth app, and the full `drive` scope (needed: `drive.file` sees only files the app created, never the VA's uploads or an existing folder) is a *restricted* scope that a public OAuth app must pass Google's security assessment for. Sharing one folder with the account limits it to that folder. https://developers.google.com/workspace/drive/api/guides/api-specific-auth
- Service accounts have no storage quota: they can make folders (no bytes) in a user's folder but uploads into My Drive fail ("Service Accounts do not have storage quota"). Folders the bot makes are owned by it; files the VA uploads stay the VA's. A Shared Drive removes both caveats. https://developers.google.com/workspace/drive/api/guides/about-shareddrives
- `thumbnailLink` is short-lived and, for a non-public file, answers only with the caller's token: hence the server-side proxy, not an `<img>` straight to Google. Its `=s220` suffix sets the size. Videos get one once Drive has processed them. https://developers.google.com/workspace/drive/api/reference/rest/v3/files
- A 404 on `files.get` means "not shared with this account" as often as "does not exist": Drive does not tell them apart.
- `files.list` of a TRASHED folder still returns its children (checked live 2026-10-21): only `folder()` (files.get `trashed`) tells a deleted folder, so /drive checks the folder before listing it. A missing/unshared parent is a 404.
- `build(..., static_discovery)` defaults to the discovery document bundled with the library: no network call to build a service.
