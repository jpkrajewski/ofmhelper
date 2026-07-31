"""The web layer's typed shapes, split by what they are a contract *with*:

- `persistence.py` -- what a job/todo/token looks like in the database.
- `generation.py` -- what a generation form posts.

Import from here; the split is an implementation detail of this package.
"""

from ofmhelpers.web.schemas.generation import ReferenceUploads
from ofmhelpers.web.schemas.persistence import ApprovalToken, Job, JobStatus, Todo

__all__ = ["ApprovalToken", "Job", "JobStatus", "ReferenceUploads", "Todo"]
