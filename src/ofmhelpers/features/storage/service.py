from pydantic import BaseModel

from ofmhelpers.features.storage.bin import StorageServiceBin
from ofmhelpers.features.storage.constants import StorageConstants
from ofmhelpers.features.storage.folders import StorageServiceFolders
from ofmhelpers.features.storage.page import StorageServicePage
from ofmhelpers.features.storage.review import StorageServiceReview
from ofmhelpers.features.storage.sheet import StorageServiceSheet
from ofmhelpers.features.storage.space import StorageServiceSpace
from ofmhelpers.features.storage.uploads import StorageServiceUploads
from ofmhelpers.features.storage.va_folders import StorageServiceVaFolders
from ofmhelpers.integrations.storage import ReadOnlyDrive
lazy from ofmhelpers.features.storage.models import StorageAccess

__all__ = [
    "StorageService",
    "StorageServiceBin",
    "StorageServiceFolders",
    "StorageServicePage",
    "StorageServiceReview",
    "StorageServiceSheet",
    "StorageServiceSpace",
    "StorageServiceUploads",
    "StorageServiceVaFolders",
]


class StorageService(BaseModel, frozen=True):
    constants: StorageConstants
    folders: StorageServiceFolders
    va_folders: StorageServiceVaFolders
    review: StorageServiceReview
    uploads: StorageServiceUploads
    space: StorageServiceSpace
    page: StorageServicePage
    bin: StorageServiceBin
    sheet: StorageServiceSheet

    @classmethod
    def compose(cls, access: StorageAccess) -> StorageService:
        owner, live, raw = access.owner_id, access.read_live, access.client
        reader = ReadOnlyDrive(raw) if live and raw is not None else raw
        person = None if access.bot else raw
        person_reader = None if access.bot else reader
        va_folders = StorageServiceVaFolders(
            client=reader, owner_id=owner, read_live=live
        )
        folders = StorageServiceFolders(
            client=reader,
            owner_id=owner,
            read_live=live,
            constants=access.constants,
            bot=access.bot,
            va_folders=va_folders,
        )
        review = StorageServiceReview(
            client=person,
            owner_id=owner,
            read_live=live,
            constants=access.constants,
            folders=folders,
        )
        return cls(
            constants=access.constants,
            folders=folders,
            va_folders=va_folders,
            review=review,
            uploads=StorageServiceUploads(
                client=person_reader,
                user_id=owner,
                constants=access.constants,
                folders=folders,
                review=review,
                va_folders=va_folders,
            ),
            space=StorageServiceSpace(
                client=person_reader,
                user_id=owner,
                read_live=live,
                constants=access.constants,
                session=access.session,
            ),
            page=StorageServicePage(constants=access.constants, folders=folders),
            bin=StorageServiceBin(
                user_id=owner,
                client=reader,
                read_live=live,
                constants=access.constants,
                folders=folders,
            ),
            sheet=StorageServiceSheet(
                client=reader,
                owner_id=owner,
                constants=access.constants,
                folders=folders,
            ),
        )
