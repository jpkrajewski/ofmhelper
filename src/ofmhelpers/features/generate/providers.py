"""The provider a generation page starts on: the one the person last ran on
it (the clone pages share theirs), if the page offers it; else the app's
default (`OFM_DEFAULT_PROVIDER`) if offered; else the page's first.

Last run, not most run: prod 2026-11-10, /clone had 362 Higgsfield runs and
62 APIYI ones, all of today's on APIYI -- a switch must stick at once."""

from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import ParamHistory

# Every page that clones a video with Wan 3.0 starts where the last one ran.
CLONE_TASKS = frozenset({"clone", "clone_links", "clone_long"})


def start_provider(actor: str, tasks: frozenset[str], offered: list[str]) -> str:
    if used := ParamHistory(tasks, actor, "provider").last(offered):
        return used
    wanted = settings.aigen.default_provider
    return wanted if wanted in offered else offered[0]
