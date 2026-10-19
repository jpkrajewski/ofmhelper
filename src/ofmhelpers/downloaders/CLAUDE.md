# downloaders

Media from external URLs.

- `generic.py` — videos via yt-dlp (`DownloadConfig`, `download`, `download_all`; TikTok H.264 re-encode, YouTube PO-token workarounds). The only place that downloads video.
- `images.py` — images via the `gallery-dl` CLI; filenames use `post_shortcode`.
- `models.py` — `DownloadResult`, `YtDlpOptions`, `YtDlpInfo`.
- `cookies.py` — `get_cookiefile()`: `cookies/cookies.txt` (`OFM_COOKIES_FILE`) or None.

Callers: `web/routers/downloads/videos.py`, `images.py`.
