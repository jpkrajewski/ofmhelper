from pydantic import BaseModel

from ofmhelpers.db.jobs.schema import Created
from ofmhelpers.db.jobs.store import count_hidden_jobs, gallery_page
lazy from ofmhelpers.db.jobs.schema import GalleryJobs


class GalleryAsk(BaseModel, frozen=True):
    tasks: frozenset[str]
    offset: int
    limit: int
    readers: frozenset[str]
    hidden: bool | None
    reader: int
    by_use: bool = False
    created: Created | None = None
    not_uploaded: bool = False


def gallery_jobs(ask: GalleryAsk) -> GalleryJobs:
    return gallery_page(
        set(ask.tasks),
        ask.offset,
        ask.limit,
        ask.readers,
        ask.hidden,
        reader=ask.reader,
        by_use=ask.by_use,
        created=ask.created,
        not_uploaded=ask.not_uploaded,
    )


def hidden_jobs(tasks: frozenset[str], readers: frozenset[str]) -> int:
    return count_hidden_jobs(set(tasks), readers)
