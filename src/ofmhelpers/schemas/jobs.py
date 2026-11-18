"""What the job endpoints hand back: to the templates, and as JSON to
generation.js / clone.js. Field names are contract with both, so they match
what the dict versions of these shapes used to carry."""

from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, computed_field

from ofmhelpers.schemas.errors import (
    ErrorText,
    download_reason,
    error_line,
    error_reason,
    error_text,
)
from ofmhelpers.schemas.persistence import JobPreview


class AssetCard(BaseModel):
    """One result file as the status page, a gallery card and the polling JS
    show it. `view_url`/`download_url` prefer kie.ai's hosted copy;
    `local_fallback_url` is our own copy to swap to once that goes stale."""

    name: str
    index: int
    kind: str
    view_url: str
    download_url: str
    download_name: str
    local_fallback_url: str | None = None
    # A video's small stand-ins for galleries (task_helpers/previews.py).
    preview_url: str | None = None
    poster_url: str | None = None
    source: str | None = None
    # A download whose video the retention sweep removed: the card shows
    # what was kept of it (`poster_url`, an image's `preview_url`; neither:
    # its name) and its Download fetches it again. `removed_reason`: why the
    # last such fetch got nothing, short (`schemas.errors.error_reason`),
    # and `removed_error` the downloader's own words; None: not tried, or
    # it is back.
    removed: bool = False
    removed_reason: str | None = None
    removed_error: str | None = None


class FailedSource(BaseModel):
    """A source URL a grouped download job could not fetch. The card shows
    each one as `link` · `reason` (owner 2026-11-15: "1 link failed" with the
    why in a tooltip nobody hovers over)."""

    source: str
    error: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def link(self) -> str:
        """The address as a person reads it: host and path, no scheme, no
        `www.`, no tracking query ("instagram.com/reel/Dc_a7TBJZ1L")."""
        parts = urlsplit(self.source)
        if not parts.netloc:
            return self.source
        host = parts.netloc.removeprefix("www.")
        return f"{host}{parts.path.rstrip('/')}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reason(self) -> str:
        """Why, worded for the card (`schemas/errors.py` `download_reason`:
        "Private account: only its followers can see it"; one no reason
        knows: the downloader's own words)."""
        return download_reason(self.error)


class JobStatusPayload(BaseModel):
    """`GET /<tool>/jobs/{id}/status`: what generation.js polls. `preview` is
    kie.ai's hosted result while the local download is still running, and
    `stage` what is being made of it meanwhile (`features/jobs/stage.py`)."""

    job_id: str
    task: str
    params: dict[str, Any]
    status: str
    error: str | None
    result: list[AssetCard]
    failed_sources: list[FailedSource]
    preview: JobPreview | None = None
    stage: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def error_reason(self) -> str:
        """The failure as one short reason, as a gallery card shows it."""
        return error_reason(self.error)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def error_line(self) -> str:
        """The failure as the card shows it: who, the reason, what they said."""
        return error_line(self.error)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def error_text(self) -> ErrorText:
        """The failure as a failed card lays it out."""
        return error_text(self.error)


class JobQueued(BaseModel):
    """`POST /<tool>/run`: the job id the page then polls."""

    job_id: str


class JobsQueued(BaseModel):
    """`POST /ai/run`: one job id per image queued (one for a batch of one),
    each drawn and polled as its own gallery card."""

    job_ids: list[str]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def job_id(self) -> str:
        """The first job, so a single-job caller reads `job_id` as before."""
        return self.job_ids[0]


class JobVisibility(BaseModel):
    """`POST /jobs/{job_id}/hidden` (static/js/hidden-cards.js): hide the
    job's card from its gallery, or show it again; answered with the
    state it is now in."""

    hidden: bool


class CloneQueued(BaseModel):
    """`POST /clone/run`, `POST /clone-links/run`: every run's job id, plus
    the batch already rendered as the page's own partial."""

    job_ids: list[str]
    html: str
    # /clone-links: the Generate's id (its Stop; a resend with it answers
    # with these runs instead of paying again). None on /clone.
    batch: str | None = None


class ReferenceAsset(BaseModel):
    """A reference file a job was given, as its status page previews it."""

    name: str
    kind: str
    view_url: str


class InputSetting(BaseModel):
    label: str
    value: Any


class InputFileGroup(BaseModel):
    label: str
    assets: list[ReferenceAsset]


class JobInputs(BaseModel):
    """A job's params split the way its status page shows them: short
    settings (a table), long text (the prompt, its own block), and reference
    file lists (previews)."""

    settings: list[InputSetting]
    long_text: list[InputSetting]
    file_groups: list[InputFileGroup]


class ActionLogEntry(BaseModel):
    """One row of the admin action log."""

    task: str
    actor: str | None
    # admin / user: the badge's colour.
    actor_role: str = "user"
    status: str
    params: dict[str, Any]
    status_url: str | None
    created_at_display: str
