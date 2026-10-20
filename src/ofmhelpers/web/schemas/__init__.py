"""The web layer's typed shapes, split by what they are a contract *with*:

- `persistence.py` -- what a job looks like in the database.
- `generation.py` -- the providers and models a generation page offers.
- `gallery.py` -- what a tool page's gallery renders for a job.
- `jobs.py` -- what the job endpoints return (status payloads, asset cards).
- `files.py` -- stored files as the reuse picker and file manager list them.
- `tools.py` -- a background-job tool as the task registry knows it.
- `applications.py` -- the landing-page application form and stored lead.
- `watched.py` -- an Instagram profile /reels rescans, with its latest scan.
- `accounts.py` -- a tracked social account (/accounts), its list query and history.

Import from here; the split is an implementation detail of this package.
"""

from ofmhelpers.web.schemas.accounts import (
    NO_CREATOR,
    AccountHistory,
    AccountPage,
    AccountQuery,
    AccountSort,
    AccountStatus,
    AccountSummary,
    AddAccounts,
    AddResult,
    AssignCreator,
    Notes,
    Period,
    SelectedAccounts,
    TrackedAccount,
)
from ofmhelpers.web.schemas.applications import Application, ApplicationForm
from ofmhelpers.web.schemas.files import DirEntry, FileEntry, RefEntry
from ofmhelpers.web.schemas.gallery import CloneBatch, CloneCard, JobCard, TaskCard
from ofmhelpers.web.schemas.generation import ModelChoice, ProviderChoice
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
    "NO_CREATOR",
    "AccountHistory",
    "AccountPage",
    "AccountQuery",
    "AccountSort",
    "AccountStatus",
    "AccountSummary",
    "ActionLogEntry",
    "AddAccounts",
    "AddResult",
    "Application",
    "ApplicationForm",
    "AssetCard",
    "AssignCreator",
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
    "ModelChoice",
    "Notes",
    "Period",
    "ProviderChoice",
    "RefEntry",
    "ReferenceAsset",
    "ResultFile",
    "SelectedAccounts",
    "TaskCard",
    "Tool",
    "TrackedAccount",
    "WatchForm",
    "WatchedProfile",
]
