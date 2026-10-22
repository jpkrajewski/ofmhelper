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


class YtDlpInfo(BaseModel):
    """The parts of yt-dlp's info dict this app reads: a playlist's entries,
    or a single entry's written files."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    type: str | None = Field(default=None, alias="_type")
    entries: list["YtDlpInfo | None"] | None = None
    requested_downloads: list[RequestedDownload] = []
    filepath: str | None = None

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
