from pydantic import Field

from ofmhelpers.db.jobs.schema import ResultFile
from ofmhelpers.features.jobs.models import TaskCard
lazy from ofmhelpers.db.drive_folders.schema import DriveUpload
lazy from ofmhelpers.media.repurpose.checker.schema import CheckReport
lazy from ofmhelpers.media.repurpose.repurpose import EncodeStep


class RepurposeResult(ResultFile):
    report: CheckReport | None = None
    attempts: int = 1
    strength: float = 1.0
    step: EncodeStep | None = None


class RepurposeCard(TaskCard):
    mirrored: bool | None = None
    turn: float | None = None
    drive_uploads: list[DriveUpload] = Field(default_factory=list)
