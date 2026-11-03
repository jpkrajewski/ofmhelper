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
- `devices.py` -- a phone (/phones) with its logins, their form bodies and list query.
- `drive.py` -- an Instagram login's Drive folders (/drive): what waits, what was posted.
- `users.py` -- who signs in (/users, /login, /account) and their role.
- `cookies.py` -- a user's own cookies.txt as /cookies shows it (no values).
- `links.py` -- a link on a link provider (/links), its list query and the Generate form.

Import from here; the split is an implementation detail of this package.
"""

from ofmhelpers.schemas.accounts import (
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
from ofmhelpers.schemas.applications import Application, ApplicationForm
from ofmhelpers.schemas.cookies import SavedCookies
from ofmhelpers.schemas.devices import (
    ArchivedLogin,
    Device,
    DeviceFields,
    DeviceLogin,
    LoginFields,
    LoginSecrets,
    PhoneQuery,
    PhoneSort,
)
from ofmhelpers.schemas.drive import (
    AccountScan,
    DriveBoard,
    DriveCard,
    DriveTarget,
    DriveUpload,
    StoredFolder,
    SyncReport,
    UploadPick,
    UploadRequest,
    UploadsQueued,
    UploadTarget,
)
from ofmhelpers.schemas.files import (
    DirEntry,
    FileEntry,
    FileTile,
    MissingRefs,
    RefEntry,
    UserFiles,
)
from ofmhelpers.schemas.gallery import CloneCard, CloneLinksCard, JobCard, TaskCard
from ofmhelpers.schemas.generation import ModelChoice, ProviderChoice
from ofmhelpers.schemas.jobs import (
    ActionLogEntry,
    AssetCard,
    CloneQueued,
    FailedSource,
    InputFileGroup,
    InputSetting,
    JobInputs,
    JobQueued,
    JobsQueued,
    JobStatusPayload,
    JobVisibility,
    ReferenceAsset,
)
from ofmhelpers.schemas.links import (
    AssignLinkCreator,
    GenerateLinks,
    LinkPage,
    LinkPeriod,
    LinkQuery,
    LinkSort,
    LinksQueued,
    LinkSummary,
    ProviderState,
    SelectedLinks,
    ShortLink,
)
from ofmhelpers.schemas.persistence import Job, JobPreview, JobStatus, ResultFile
from ofmhelpers.schemas.pictures import PicturePreset
from ofmhelpers.schemas.tools import Tool
from ofmhelpers.schemas.users import (
    ApiKeysForm,
    LinkedAccount,
    PasswordForm,
    Role,
    SessionUser,
    User,
    UserForm,
)
from ofmhelpers.schemas.viral import ViralFile, ViralReel, ViralSearch
from ofmhelpers.schemas.watched import (
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
    "AccountScan",
    "AccountSort",
    "AccountStatus",
    "AccountSummary",
    "ActionLogEntry",
    "AddAccounts",
    "AddResult",
    "ApiKeysForm",
    "Application",
    "ApplicationForm",
    "ArchivedLogin",
    "AssetCard",
    "AssignCreator",
    "AssignLinkCreator",
    "CloneCard",
    "CloneLinksCard",
    "CloneQueued",
    "Device",
    "DeviceFields",
    "DeviceLogin",
    "DirEntry",
    "DriveBoard",
    "DriveCard",
    "DriveTarget",
    "DriveUpload",
    "FailedSource",
    "FeedItem",
    "FileEntry",
    "FileTile",
    "GenerateLinks",
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
    "JobVisibility",
    "JobsQueued",
    "Leaderboard",
    "LinkPage",
    "LinkPeriod",
    "LinkQuery",
    "LinkSort",
    "LinkSummary",
    "LinkedAccount",
    "LinksQueued",
    "LoginFields",
    "LoginSecrets",
    "MissingRefs",
    "ModelChoice",
    "Notes",
    "PasswordForm",
    "Period",
    "PhoneQuery",
    "PhoneSort",
    "PicturePreset",
    "ProviderChoice",
    "ProviderState",
    "RefEntry",
    "ReferenceAsset",
    "ResultFile",
    "Role",
    "SavedCookies",
    "SelectedAccounts",
    "SelectedLinks",
    "SessionUser",
    "ShortLink",
    "StoredFolder",
    "SyncReport",
    "TaskCard",
    "Tool",
    "TrackedAccount",
    "UploadPick",
    "UploadRequest",
    "UploadTarget",
    "UploadsQueued",
    "User",
    "UserFiles",
    "UserForm",
    "ViralFile",
    "ViralReel",
    "ViralSearch",
    "WatchForm",
    "WatchedProfile",
]
