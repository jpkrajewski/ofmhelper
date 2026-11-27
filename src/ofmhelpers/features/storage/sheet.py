# Copyright (C) 2026 jpkrajewski

import io
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import logfire
from pydantic import BaseModel, SkipValidation
lazy from openpyxl import Workbook

from ofmhelpers.cache.lock import lock_held
from ofmhelpers.config import settings
from ofmhelpers.features.refusals import RefusedError, UnavailableError
from ofmhelpers.features.storage.constants import StorageConstants
from ofmhelpers.features.storage.folders import StorageServiceFolders
from ofmhelpers.features.storage.models import Sheet
from ofmhelpers.integrations.storage import (
    DriveAuthError,
    DriveError,
    StorageClient,
)
lazy from ofmhelpers.db.drive_folders.schema import DriveFile

logger = logging.getLogger(__name__)


class StorageServiceSheet(BaseModel, frozen=True, arbitrary_types_allowed=True):
    client: SkipValidation[StorageClient] | None
    owner_id: int
    constants: StorageConstants
    folders: StorageServiceFolders

    def _blank(self, title: str) -> bytes:
        book = Workbook()
        tab = book.worksheets[0]
        tab.title = title
        tab["A1"] = datetime.now(ZoneInfo(settings.gdrive.timezone)).date()
        out = io.BytesIO()
        book.save(out)
        return out.getvalue()

    def _found_or_made(self, client: StorageClient, name: str) -> DriveFile:
        root = self.folders.ensure_root()
        with lock_held(
            f"storage:sheet:{self.owner_id}",
            settings.storage.start_lock_s,
        ) as held:
            found = client.find_sheet(root, name)
            if found is not None:
                return found
            if not held:
                logger.error(
                    "storage sheet %r of %s not made: lock not held",
                    name,
                    self.owner_id,
                )
                raise UnavailableError(self.constants.sheet_failed)
            made = client.create_sheet(root, name, self._blank(name))
        logger.info("storage sheet %r made for %s (%s)", name, self.owner_id, made.id)
        return made

    def get_by_name(self, name: str) -> Sheet:
        with logfire.span("drive sheet {sheet}", sheet=name, drive_owner=self.owner_id):
            try:
                if self.client is None:
                    raise RefusedError(self.constants.sheet_no_drive)
                read = self.client.sheet(self._found_or_made(self.client, name).id)
            except DriveAuthError as exc:
                raise RefusedError(str(exc)) from exc
            except DriveError as exc:
                logger.warning(
                    "drive sheet %r of %s not read",
                    name,
                    self.owner_id,
                    exc_info=True,
                )
                raise UnavailableError(self.constants.sheet_failed) from exc
        return Sheet(name=name, tabs=read.tabs)
