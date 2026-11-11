"""What one source URL's download comes back as: the downloaders
(`integrations/downloaders`) return it and a grouped download job stores a
list of them."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class DownloadResult(BaseModel):
    """One source URL: whether it downloaded, and the files it produced (a
    post can hold several), or why it failed. A grouped download job stores a
    list of these, dumped with `mode="json"` so the paths become strings."""

    model_config = ConfigDict(extra="forbid")

    url: str
    success: bool
    output_paths: list[Path] = []
    error: str | None = None
