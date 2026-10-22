# downloaders

Media from external URLs.

- `generic.py` — videos via yt-dlp (`DownloadConfig`, `download`, `download_all`; TikTok H.264 re-encode, YouTube PO-token workarounds). The only place that downloads video.
  - `_PROXIED_SITES` (Instagram: `OFM_IG_PROXIES`, 1 + `OFM_IG_PROXY_RETRIES` tries): metadata logged out via a proxy, file direct from the CDN, then the cookies + direct try last. Verified on prod 2026-10-21: server IP logged out -> "redirected to the login page"; cookies.txt -> `HTTP Error 400`; proxy logged out -> OK.
- `images.py` — images via the `gallery-dl` CLI; filenames use `post_shortcode`. `_LOGIN_WALLS` turns a login-redirect error into "upload your fresh cookies.txt at /cookies".
  - Instagram photo/carousel posts need a working login. Verified 2026-10-22 on `/p/DGUaB0Rtazx`: gallery-dl 1.32.17 logged out (direct, via `OFM_IG_PROXIES`, `api=graphql`) -> "HTTP redirect to login page"; the logged-out post page carries only the cover image (1 `image_versions2`, no `video_versions`); yt-dlp -> "There is no video in this post" (prod via proxy: "No video formats found!"). Not a yt-dlp bug: it downloads video only.
- `media.py` — any link (/download-media): yt-dlp then gallery-dl, or gallery-dl first for photo-post links (`_IMAGES_FIRST`: Instagram `/p/`, X/Twitter, Pinterest, TikTok `/photo/`); first that gets files wins, both errors kept otherwise.
- `models.py` — `DownloadResult`, `YtDlpOptions`, `YtDlpInfo`.
- `fake.py` — OFM_FAKE_EXTERNALS: a URL "downloads" to a generated mp4 (video) or two jpgs (post); "fake-fail" in the URL fails.
- `cookies.py` — cookies.txt as text, never a shared file (2026-10-22): each user's own lives encrypted in Postgres (`web/stores/cookies.py`) and the caller passes it in. `cookie_file(text)` = 0600 temp file for yt-dlp/gallery-dl, deleted after the run (yt-dlp's write-back is discarded); `load_jar(text)` for `requests`; `summarize(text)` -> `CookieJarSummary` (count, domains, Instagram `sessionid` + expiry; never a value), `CookieFileError` for anything not Netscape cookies.txt. `download_all(urls, cookies=...)` in all three modules; `download(url, ..., cookiefile)`. `OFM_COOKIES_FILE` is only read by alembic b1c2d3e4f5a7 (moved to the password admin's own cookies; file kept).

Callers: `web/routers/downloads/media.py` (the Download tool), `videos.py`/`images.py` (old jobs' routes only).
