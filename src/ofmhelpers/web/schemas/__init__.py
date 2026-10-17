"""The web layer's typed shapes, split by what they are a contract *with*:

- `persistence.py` -- what a job looks like in the database.
- `generation.py` -- what a generation form posts.
- `gallery.py` -- what a tool page's gallery renders for a job.
- `jobs.py` -- what the job endpoints return (status payloads, asset cards).
- `files.py` -- stored files as the reuse picker and file manager list them.
- `tools.py` -- a background-job tool as the task registry knows it.
- `applications.py` -- the landing-page application form and stored lead.
- `watched.py` -- an Instagram profile /reels rescans, with its latest scan.

Import from here; the split is an implementation detail of this package.
"""

from ofmhelpers.web.schemas.applications import Application, ApplicationForm
from ofmhelpers.web.schemas.files import DirEntry, FileEntry, RefEntry
from ofmhelpers.web.schemas.gallery import CloneBatch, CloneCard, JobCard, TaskCard
from ofmhelpers.web.schemas.generation import ReferenceUploads
from ofmhelpers.web.schemas.jobs import (
    ActionLogEntry,
    AssetCard,
    CloneQueued,
    FailedSource,
    InputFileGroup,
    InputSetting,
    JobInputs,
    JobQueued,
    JobStatusPayload,
    ReferenceAsset,
)
from ofmhelpers.web.schemas.persistence import Job, JobPreview, JobStatus, ResultFile
from ofmhelpers.web.schemas.tools import Tool
from ofmhelpers.web.schemas.watched import (
    FeedItem,
    GroupForm,
    Leaderboard,
    WatchedProfile,
    WatchForm,
)

__all__ = [
    "ActionLogEntry",
    "Application",
    "ApplicationForm",
    "AssetCard",
    "CloneBatch",
    "CloneCard",
    "CloneQueued",
    "DirEntry",
    "FailedSource",
    "FeedItem",
    "FileEntry",
    "GroupForm",
    "InputFileGroup",
    "InputSetting",
    "Job",
    "JobCard",
    "JobInputs",
    "JobPreview",
    "JobQueued",
    "JobStatus",
    "JobStatusPayload",
    "Leaderboard",
    "RefEntry",
    "ReferenceAsset",
    "ReferenceUploads",
    "ResultFile",
    "TaskCard",
    "Tool",
    "WatchForm",
    "WatchedProfile",
]
