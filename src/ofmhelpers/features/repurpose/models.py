# Copyright (C) 2026 jpkrajewski

from pydantic import Field

from ofmhelpers.features.jobs.models import TaskCard
lazy from ofmhelpers.db.drive_folders.schema import DriveUpload


class RepurposeCard(TaskCard):
    mirrored: bool | None = None
    turn: float | None = None
    drive_uploads: list[DriveUpload] = Field(default_factory=list)
