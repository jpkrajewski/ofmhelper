"""The downloaders' data: what one source URL's download comes back as, and
the two shapes yt-dlp speaks -- the options it takes and the info it returns.
yt-dlp itself only takes and gives dicts; these models are converted at that
boundary (`YtDlpOptions.model_dump(exclude_none=True)`,
`YtDlpInfo.model_validate(info)`)."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_serializer
lazy from pydantic import SerializerFunctionWrapHandler

from ofmhelpers.schemas.cookies import CookieJarSummary, CookieSite, SiteCookies
from ofmhelpers.schemas.downloads import DownloadResult

__all__ = [
    "CookieJarSummary",
    "CookieSite",
    "DownloadResult",
    "ExtractorArgs",
    "RequestedDownload",
    "SiteCookies",
    "VideoInfo",
    "YtDlpFormat",
    "YtDlpInfo",
    "YtDlpOptions",
]

# yt-dlp's per-extractor arguments: extractor -> argument -> values.
ExtractorArgs = dict[str, dict[str, list[str]]]


class VideoInfo(BaseModel):
    """A link's video as yt-dlp sees it without downloading it: its length
    and cover image URL, or why it could not be read (`error`)."""

    model_config = ConfigDict(extra="forbid")

    url: str
    seconds: float | None = None
    thumbnail: str | None = None
    # Why it could not be read: the last try's reason (the login, when the
    # site has one), and the first logged-out try's when that differed.
    error: str | None = None
    logged_out_error: str | None = None


class YtDlpOptions(BaseModel):
    """The yt-dlp options a `DownloadConfig` resolves to. What
    `yt_dlp.YoutubeDL` takes is `model_dump(exclude_none=True)`: unset
    options are left out so yt-dlp's own defaults apply."""

    format: str
    merge_output_format: str
    outtmpl: str
    writethumbnail: bool
    noplaylist: bool
    # Which of a playlist's entries are read ("1:10" = the first ten).
    playlist_items: str | None = None
    # yt-dlp's names (regexes) of the extractors it may use; None = all its
    # default ones, Generic (any page of any host) included.
    allowed_extractors: list[str] | None = None
    # Seconds a connection may sit silent (yt-dlp's default: 20).
    socket_timeout: float | None = None
    quiet: bool = True
    no_warnings: bool = False
    remote_components: set[str] = {"ejs:github"}
    cookiefile: str | None = None
    # yt-dlp wants (browser, profile, keyring, container).
    cookiesfrombrowser: tuple[str, None, None, None] | None = None
    proxy: str | None = None
    extractor_args: ExtractorArgs = {}

    @model_serializer(mode="wrap")
    def _without_empty_args(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        """No extractor arguments = the option left out, like an unset one."""
        opts: dict[str, Any] = handler(self)
        if not self.extractor_args:
            opts.pop("extractor_args", None)
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
    entries: list[YtDlpInfo | None] | None = None
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

    def first_video(self) -> YtDlpInfo | None:
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
