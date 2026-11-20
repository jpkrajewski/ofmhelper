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

ExtractorArgs = dict[str, dict[str, list[str]]]


class VideoInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    seconds: float | None = None
    thumbnail: str | None = None
    error: str | None = None
    logged_out_error: str | None = None


class YtDlpOptions(BaseModel):
    format: str
    merge_output_format: str
    outtmpl: str
    writethumbnail: bool
    noplaylist: bool
    playlist_items: str | None = None
    allowed_extractors: list[str] | None = None
    socket_timeout: float | None = None
    quiet: bool = True
    no_warnings: bool = False
    remote_components: set[str] = {"ejs:github"}
    cookiefile: str | None = None
    cookiesfrombrowser: tuple[str, None, None, None] | None = None
    proxy: str | None = None
    extractor_args: ExtractorArgs = {}

    @model_serializer(mode="wrap")
    def _without_empty_args(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        opts: dict[str, Any] = handler(self)
        if not self.extractor_args:
            opts.pop("extractor_args", None)
        return opts


class RequestedDownload(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    filepath: str | None = None
    filename: str | None = Field(default=None, alias="_filename")


class YtDlpFormat(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str | None = None
    vcodec: str | None = None


class YtDlpInfo(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    type: str | None = Field(default=None, alias="_type")
    entries: list[YtDlpInfo | None] | None = None
    requested_downloads: list[RequestedDownload] = []
    filepath: str | None = None
    duration: float | None = None
    thumbnail: str | None = None
    url: str | None = None
    vcodec: str | None = None
    requested_formats: list[YtDlpFormat] = []

    def video_url(self) -> str | None:
        streams = self.requested_formats or [
            YtDlpFormat(url=self.url, vcodec=self.vcodec)
        ]
        return next((f.url for f in streams if f.url and f.vcodec != "none"), None)

    def first_video(self) -> YtDlpInfo | None:
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
