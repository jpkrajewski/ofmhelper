"""
The ONLY code allowed to touch the database, one module per domain.

`db/stores/*` keep their existing public function signatures and delegate
their bodies to the repositories here, so the ~13 routers that call those
functions never change.

Each method runs in its own `session_scope` unit of work. Status/field
updates are single UPDATE statements (atomic) -- the read-modify-write race
the old JSON files documented (two jobs finishing at once losing an update)
is gone.

A method returns Pydantic models (`schemas/`), a list of them, None or one
plain value: never a row, a dict or a tuple. The stores pass them on.

Reads are cached (see cached_repository.py); `@cached` marks a read method
(Redis holds the JSON of what its return annotation names),
`@invalidates_cache` marks a write method. Repositories never touch
`self._cache` directly -- that plumbing lives in `CachedRepository` and the
two decorators.

Import the repositories from here rather than from a submodule; the split is
by domain and a domain may grow a second module.
"""

from ofmhelpers.db.repositories.applications import ApplicationRepository
from ofmhelpers.db.repositories.chats import ChatRepository
from ofmhelpers.db.repositories.clone_ai_checks import CloneAiCheckRepository
from ofmhelpers.db.repositories.clone_examples import CloneExampleRepository
from ofmhelpers.db.repositories.clone_from_links import CloneFromLinkRepository
from ofmhelpers.db.repositories.cookies import CookieRepository
from ofmhelpers.db.repositories.database import DatabaseRepository
from ofmhelpers.db.repositories.devices import DeviceRepository, DuplicateError
from ofmhelpers.db.repositories.downloads import DownloadRepository
from ofmhelpers.db.repositories.drive_folders import DriveFolderRepository
from ofmhelpers.db.repositories.drive_uploads import DriveUploadRepository
from ofmhelpers.db.repositories.jobs import JobRepository
from ofmhelpers.db.repositories.phone_revisions import PhoneRevisionRepository
from ofmhelpers.db.repositories.picture_presets import PicturePresetRepository
from ofmhelpers.db.repositories.report_runs import ReportRunRepository
from ofmhelpers.db.repositories.self_links import SelfLinkRepository
from ofmhelpers.db.repositories.short_links import ShortLinkRepository
from ofmhelpers.db.repositories.tracked_accounts import TrackedAccountRepository
from ofmhelpers.db.repositories.users import OwnsDataError, UserRepository
from ofmhelpers.db.repositories.watched_profiles import WatchedProfileRepository

__all__ = [
    "ApplicationRepository",
    "ChatRepository",
    "CloneAiCheckRepository",
    "CloneExampleRepository",
    "CloneFromLinkRepository",
    "CookieRepository",
    "DatabaseRepository",
    "DeviceRepository",
    "DownloadRepository",
    "DriveFolderRepository",
    "DriveUploadRepository",
    "DuplicateError",
    "JobRepository",
    "OwnsDataError",
    "PhoneRevisionRepository",
    "PicturePresetRepository",
    "ReportRunRepository",
    "SelfLinkRepository",
    "ShortLinkRepository",
    "TrackedAccountRepository",
    "UserRepository",
    "WatchedProfileRepository",
]
