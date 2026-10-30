"""Trial reels: a model's POSTED reels -> snapchat-pipeline copies with
AI-written captions -> approved -> a TRIALS folder next to POSTED in her
Drive folder (`web/routers/video_tools/trials.py`)."""

from ofmhelpers.trials.captions import caption_for, understand
from ofmhelpers.trials.state import FolderState, ReelState, load, save, work_dir

__all__ = [
    "FolderState",
    "ReelState",
    "caption_for",
    "load",
    "save",
    "understand",
    "work_dir",
]
