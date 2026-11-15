"""/edit's clips (owner 2026-11-15: "cut a selected piece of video, inverse,
so we can remove video from the video", "clips adding ... and moving like in
capcut"): one track of parts, each a stretch of a stored video, played one
after another. The page keeps the list (add a video, drag to move, keep or
remove the selection: removing the middle of a clip leaves the part before
and the part after); a Cut sends it whole and the edit worker joins them.

One clip is the cut there always was (`run_cut` with `video` / `start` /
`end`, so cuts queued before still run); the limits are the same for the
whole list: each clip at least `edit_min_s`, all of them together at most
`edit_max_part_s` of source, at most `edit_max_clips` clips."""

from pydantic import BaseModel, ConfigDict, Field

from ofmhelpers.config import settings
from ofmhelpers.features.edit.errors import EditRefusedError


class Clip(BaseModel):
    """`start`..`end` seconds of the stored `video`."""

    model_config = ConfigDict(frozen=True)

    video: str
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)

    @property
    def length(self) -> float:
        return self.end - self.start


def checked(clips: list[Clip], lengths: list[float]) -> list[Clip]:
    """`clips` within their videos (`lengths`: each one's seconds, in
    order), refused when one is too short or all are too long or too many."""
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
