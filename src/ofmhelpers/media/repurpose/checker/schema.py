from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel


class AudioScore(BaseModel, frozen=True):
    similarity: float
    offset_s: float
    threshold: float
    matched_share: float


class TmkScore(BaseModel, frozen=True):
    level1: float
    level2: float
    threshold: float

    @property
    def matched(self) -> bool:
        return self.level1 >= self.threshold and self.level2 >= self.threshold


class HashKind(StrEnum):
    PDQ = "pdq"
    PHASH = "phash"
    DHASH = "dhash"
    WHASH = "whash"


class SequenceMatch(BaseModel, frozen=True):
    share: float
    scale: float
    offset_s: float


class HashScore(BaseModel, frozen=True):
    kind: HashKind
    bits: int
    threshold: int
    nearest: list[int]
    median: float
    minimum: int
    matched_share: float
    sequence: SequenceMatch


class DescriptorScore(BaseModel, frozen=True):
    kind: str = "sscd"
    threshold: float
    nearest: list[float]
    median: float
    matched_share: float
    sequence: SequenceMatch


class CheckReport(BaseModel, frozen=True):
    source_file: Path
    copy_file: Path
    source_frames: int
    copy_frames: int
    flat_copy_frames: int
    hashes: list[HashScore]
    descriptors: list[DescriptorScore] = []
    tmk: TmkScore | None = None
    audio: AudioScore | None
    looks_like_source: bool
    sounds_like_source: bool | None

    @property
    def unique(self) -> bool:
        return not self.looks_like_source and not self.sounds_like_source
