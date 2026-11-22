import logfire
from pydantic import BaseModel, SkipValidation

from ofmhelpers.features.refusals import NotFoundError, RefusedError, UnavailableError
from ofmhelpers.features.storage.constants import storage_constants
from ofmhelpers.features.storage.folders import StorageServiceFolders
from ofmhelpers.features.storage.models import Sheet
from ofmhelpers.integrations.storage import (
    RECONNECT,
    DriveAuthError,
    DriveError,
    StorageClient,
)
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


class StorageServiceSheet(BaseModel, frozen=True, arbitrary_types_allowed=True):
    client: SkipValidation[StorageClient] | None
    owner_id: int
    folders: StorageServiceFolders

    def get_by_name(self, name: str) -> Sheet:
        with logfire.span("drive sheet {sheet}", sheet=name, drive_owner=self.owner_id):
            try:
                if self.client is None:
                    raise RefusedError(storage_constants().sheet_no_drive)
                root = self.folders.find_root()
                file = self.client.find_file(root, name) if root else None
                if file is None:
                    where = self.folders.root_name()
                    msg = f"No sheet named {name} in {where} in Google Drive"
                    raise NotFoundError(msg)
                read = self.client.sheet(file.id)
            except DriveAuthError as exc:
                raise RefusedError(RECONNECT) from exc
            except DriveError as exc:
                logger.warning(
                    "drive sheet %r of %s not read", name, self.owner_id, exc_info=True
                )
                raise UnavailableError(storage_constants().sheet_failed) from exc
        return Sheet(name=name, tabs=read.tabs)
