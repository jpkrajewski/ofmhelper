from enum import StrEnum
from functools import lru_cache

from pydantic import BaseModel


class TrialsPhase(StrEnum):
    GENERATE = "Making trials"
    REMAKE = "Making one trial"
    CAPTIONS = "Writing captions"
    UPLOAD = "Uploading"


class TrialsConstants(BaseModel, frozen=True):
    nothing_new: str = "No new reels in POSTED"


@lru_cache(maxsize=1)
def trials_constants() -> TrialsConstants:
    return TrialsConstants()
