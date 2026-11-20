from pydantic import BaseModel, ConfigDict, Field

from ofmhelpers.config import settings
from ofmhelpers.features.refusals import EditRefusedError


class Clip(BaseModel):
    model_config = ConfigDict(frozen=True)

    video: str
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)

    @property
    def length(self) -> float:
        return self.end - self.start


def checked(clips: list[Clip], lengths: list[float]) -> list[Clip]:
    web = settings.web
    if not clips:
        msg = "Add a video first"
        raise EditRefusedError(msg)
    if len(clips) > web.edit_max_clips:
        msg = f"At most {web.edit_max_clips} clips"
        raise EditRefusedError(msg)
    fitted = [
        clip.model_copy(update={"end": min(clip.end, seconds)})
        for clip, seconds in zip(clips, lengths, strict=True)
    ]
    if any(clip.length < web.edit_min_s for clip in fitted):
        msg = f"Pick at least {web.edit_min_s:g} s"
        raise EditRefusedError(msg)
    if sum(clip.length for clip in fitted) > web.edit_max_part_s:
        msg = f"Pick at most {web.edit_max_part_s:g} s"
        raise EditRefusedError(msg)
    return fitted
