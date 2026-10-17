# Module purpose

Downloading media from external URLs: videos via yt-dlp, images via
gallery-dl, plus shared cookie-file handling for authenticated sites
(Instagram etc).

# Module files

- `generic.py` — video downloader. `DownloadConfig` (format, output dir,
  cookies, TikTok-specific H.264 re-encode settings, YouTube PO-token /
  bgutil-pot-provider workarounds); `download(url, config)` /
  `download_all(urls, config)` wrap `yt_dlp` (lazy
  import — degrades to a clear error if yt-dlp isn't installed). Do not
  reimplement downloading elsewhere, call this.
- `images.py` — image downloader (`ImageDownloadConfig` + `download_all`),
  shells out to the `gallery-dl` CLI via `subprocess`.
  Filenames disambiguated by `post_shortcode` (Instagram's real unique ID),
  not yt-dlp's `{id}`.
- `models.py` — the data: `DownloadResult` (one source URL's outcome, both
  downloaders return it), `YtDlpOptions` (what yt-dlp is given, as a dict via
  `to_ydl()`) and `YtDlpInfo` (the parts of yt-dlp's info dict read back,
  `output_paths()`).
- `cookies.py` — `get_cookiefile()`: returns the path to `cookies/cookies.txt`
  (override via `OFM_COOKIES_FILE`) if it exists, else `None`. Both
  downloaders check this before falling back to `cookies_from_browser`.

# Who calls this

`web/routers/downloads/videos.py` (video), `download_images.py` (images).
