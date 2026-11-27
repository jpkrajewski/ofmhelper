# Copyright (C) 2026 jpkrajewski

from pydantic import Field

from ofmhelpers.features.jobs.models import TaskCard
from ofmhelpers.features.repurpose.constants import MirrorChoice
lazy from ofmhelpers.db.drive_folders.schema import DriveUpload


class RepurposeCard(TaskCard):
    mirrored: bool | None = None
    turn: float | None = None
    preset: str | None = None
    strength: float | None = None
    picture_tries: int | None = None
    audio_tries: int | None = None
    mirror_choice: MirrorChoice = MirrorChoice.RANDOM
    kept_first_sound: bool = False
    creator_id: int | None = None
    creator_name: str | None = None
    drive_uploads: list[DriveUpload] = Field(default_factory=list)
