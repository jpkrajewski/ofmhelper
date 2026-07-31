"""
The ONLY code allowed to touch the database, one module per domain.

`web/stores/*` keep their existing public function signatures and delegate
their bodies to the repositories here, so the ~13 routers that call those
functions never change.

Each method runs in its own `session_scope` unit of work. Status/field
updates are single UPDATE statements (atomic) -- the read-modify-write race
the old JSON files documented (two jobs finishing at once losing an update)
is gone.

Return values are plain dicts in the exact shape the old in-memory dicts had,
so callers (templates, task_helpers, routers) are unaffected.

Reads are cached (see cached_repository.py); `@cached` marks a read method,
`@invalidates_cache` marks a write method. Repositories never touch
`self._cache` directly -- that plumbing lives in `CachedRepository` and the
two decorators.

Import the repositories from here rather than from a submodule; the split is
by domain and a domain may grow a second module.
"""

from ofmhelpers.web.db.repositories.approval_tokens import ApprovalTokenRepository
from ofmhelpers.web.db.repositories.instagram_stats import InstagramStatsRepository
from ofmhelpers.web.db.repositories.jobs import JobRepository
from ofmhelpers.web.db.repositories.models import ModelRepository
from ofmhelpers.web.db.repositories.todos import TodoRepository

__all__ = [
    "ApprovalTokenRepository",
    "InstagramStatsRepository",
    "JobRepository",
    "ModelRepository",
    "TodoRepository",
]
