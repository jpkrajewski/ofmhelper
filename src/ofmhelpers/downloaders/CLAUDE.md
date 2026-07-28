# Module purpose

Downloading media from external URLs: videos via yt-dlp, images via
gallery-dl, plus shared cookie-file handling for authenticated sites
(Instagram etc).

# Module files

- `generic.py` — video downloader. `DownloadConfig` (format, output dir,
  cookies, TikTok-specific H.264 re-encode settings, YouTube PO-token /
  bgutil-pot-provider workarounds) + `DownloadResult` dataclasses;
  `download(url, config)` / `download_all(urls, config)` wrap `yt_dlp` (lazy
  import — degrades to a clear error if yt-dlp isn't installed). Reused by
  `reel_machine/intake.py` for the reel-fetch step — do not reimplement
  downloading elsewhere, call this.
- `images.py` — image downloader (`ImageDownloadConfig` / `ImageDownloadResult`
  + `download_all`), shells out to the `gallery-dl` CLI via `subprocess`.
  Filenames disambiguated by `post_shortcode` (Instagram's real unique ID),
  not yt-dlp's `{id}`.
- `cookies.py` — `get_cookiefile()`: returns the path to `cookies/cookies.txt`
  (override via `OFM_COOKIES_FILE`) if it exists, else `None`. Both
  downloaders check this before falling back to `cookies_from_browser`.

# Who calls this

`web/routers/downloads/videos.py` (video), `download_images.py` (images),
`reel_machine/intake.py` (reel fetch — reuses `generic.download`, never a
separate HTTP/yt-dlp call).
