"""The downloaders' data: what one source URL's download comes back as, and
the two shapes yt-dlp speaks -- the options it takes and the info it returns.
yt-dlp itself only takes and gives dicts; these models are converted at that
boundary (`YtDlpOptions.to_ydl()`, `YtDlpInfo.model_validate(info)`)."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# yt-dlp's per-extractor arguments: extractor -> argument -> values.
ExtractorArgs = dict[str, dict[str, list[str]]]


class DownloadResult(BaseModel):
    """One source URL: whether it downloaded, and the files it produced (a
    post can hold several), or why it failed. A grouped download job stores a
    list of these, dumped with `mode="json"` so the paths become strings."""

    model_config = ConfigDict(extra="forbid")

    url: str
    success: bool
    output_paths: list[Path] = []
    error: str | None = None


class VideoInfo(BaseModel):
    """A link's video as yt-dlp sees it without downloading it: its length
    and cover image URL, or why it could not be read (`error`)."""

    model_config = ConfigDict(extra="forbid")

    url: str
    seconds: float | None = None
    thumbnail: str | None = None
    error: str | None = None


class YtDlpOptions(BaseModel):
    """The yt-dlp options a `DownloadConfig` resolves to."""

    format: str
    merge_output_format: str
    outtmpl: str
    writethumbnail: bool
    noplaylist: bool
    quiet: bool = True
    no_warnings: bool = False
    remote_components: set[str] = {"ejs:github"}
    cookiefile: str | None = None
    # yt-dlp wants (browser, profile, keyring, container).
    cookiesfrombrowser: tuple[str, None, None, None] | None = None
    proxy: str | None = None
    extractor_args: ExtractorArgs = {}

    def to_ydl(self) -> dict[str, Any]:
        """The dict `yt_dlp.YoutubeDL` takes; unset options are left out so
        yt-dlp's own defaults apply."""
        opts = self.model_dump(exclude_none=True)
        if not self.extractor_args:
            del opts["extractor_args"]
        return opts


class RequestedDownload(BaseModel):
    """One file yt-dlp wrote for an entry (after merging/postprocessing)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    filepath: str | None = None
    filename: str | None = Field(default=None, alias="_filename")


class YtDlpFormat(BaseModel):
    """One format yt-dlp picked for an entry (a DASH video, its audio)."""

    model_config = ConfigDict(extra="ignore")

    url: str | None = None
    vcodec: str | None = None


class YtDlpInfo(BaseModel):
    """The parts of yt-dlp's info dict this app reads: a playlist's entries,
    or a single entry's written files."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    type: str | None = Field(default=None, alias="_type")
    entries: list["YtDlpInfo | None"] | None = None
    requested_downloads: list[RequestedDownload] = []
    filepath: str | None = None
    # What a look without downloading reads (`generic.video_info`). Some
    # extractors give no `duration` (Instagram logged out, 2026-10-25): the
    # length is then probed from the video stream's URL.
    duration: float | None = None
    thumbnail: str | None = None
    url: str | None = None
    vcodec: str | None = None
    requested_formats: list[YtDlpFormat] = []

    def video_url(self) -> str | None:
        """Where its video stream is: the picked video format's URL."""
        streams = self.requested_formats or [
            YtDlpFormat(url=self.url, vcodec=self.vcodec)
        ]
        return next((f.url for f in streams if f.url and f.vcodec != "none"), None)

    def first_video(self) -> "YtDlpInfo | None":
        """The entry that is a video: itself, or a playlist's first (a
        carousel post's first video)."""
        entries = self.entries if self.type == "playlist" else [self]
        return next(
            (
                e
                for e in entries or []
                if e is not None and (e.duration is not None or e.video_url())
            ),
            None,
        )

    def output_paths(self) -> list[Path]:
        """The real output file(s): what yt-dlp itself considers final after
        any merging or postprocessing -- far more reliable than parsing log
        text, which doesn't emit the same line for every extractor/format."""
        entries = self.entries if self.type == "playlist" else [self]
        paths: list[Path] = []
        for entry in entries or []:
            if entry is not None:
                paths += entry.own_paths()
        return paths

    def own_paths(self) -> list[Path]:
        if not self.requested_downloads:
            return [Path(self.filepath)] if self.filepath else []
        return [
            Path(fp)
            for rd in self.requested_downloads
            if (fp := rd.filepath or rd.filename)
        ]


class CookieSite(BaseModel):
    """A site whose login a cookies.txt may carry: its cookie domains (and
    their subdomains) and the cookie that means "logged in". From
    phones/services.json (`LoginService.cookies`)."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    icon_url: str = ""
    domains: list[str]
    login: str

    def owns(self, domain: str) -> bool:
        host = domain.lstrip(".").lower()
        return any(host == d or host.endswith(f".{d}") for d in self.domains)


class SiteCookies(BaseModel):
    """One `CookieSite` in an uploaded cookies.txt: its cookies' names
    (never a value) and whether, and until when, it is logged in."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    icon_url: str = ""
    names: list[str] = []
    logged_in: bool = False
    # When the login cookie expires (epoch s); None without one, or a
    # session cookie.
    expires_at: float | None = None


class CookieJarSummary(BaseModel):
    """What /cookies shows about an uploaded cookies.txt: never a value,
    only which sites it covers and whether it holds an Instagram login."""

    model_config = ConfigDict(extra="forbid")

    # Per known site (services.json), in its order: logged in or not.
    sites: list[SiteCookies] = []
    # Cookies of every other site, only counted.
    other: int = 0

    cookies: int
    # Registrable-ish hosts, leading dot dropped, sorted.
    domains: list[str]
    # A `sessionid` cookie for instagram.com: what every logged-in
    # Instagram call needs.
    instagram_login: bool
    # When that login cookie expires (epoch s); None without one, or a
    # session cookie.
    instagram_expires_at: float | None = None
