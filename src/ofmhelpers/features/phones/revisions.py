"""/phones History: the person's phones as they were after each change, and
going back to one.

The revisions are written by the database (alembic b7c8d9e0f1a3: a trigger
on the three phone tables, a second one that makes the history append-only),
so nothing here records. A restore puts the stored rows back, passwords as
their encrypted text; the database records the result as one more revision,
so a restore is undone by restoring the one before it.

A restore changes the phone tables only. Google Drive is never touched: an
account that comes back is tracked on /accounts again and gets its folder at
the next Drive sync; an account that goes away keeps its folder and its
/accounts row.
"""

from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.db.stores import devices as device_store
from ofmhelpers.db.stores import phone_revisions as store
from ofmhelpers.features.accounts.tracker import track_login
from ofmhelpers.features.refusals import NotFoundError
from ofmhelpers.log import get_logger
lazy from ofmhelpers.schemas.phone_revisions import PhoneRevision, Restored

logger = get_logger(__name__)


class Revisions(BaseModel, frozen=True):
    """The History of the person asking (`scope`)."""

    scope: int

    def listed(self) -> list[PhoneRevision]:
        """Newest first, the newest `OFM_PHONES_HISTORY_LIMIT`."""
        return store.page(self.scope, settings.vault.history_limit)

    def restore(self, revision_id: int) -> Restored:
        done = store.restore(revision_id, self.scope)
        if done is None:
            msg = "No such revision"
            raise NotFoundError(msg)
        logger.info(
            "phones of user %s restored to revision %s (%d logins back)",
            self.scope,
            revision_id,
            len(done.logins_back),
        )
        self._track(set(done.logins_back))
        return done

    def _track(self, back: set[int]) -> None:
        """The accounts a restore brought back show on /accounts again (they
        left it when their login was deleted)."""
        if not back:
            return
        for device in device_store.list_devices(self.scope):
            for login in device.logins:
                if login.id in back:
                    track_login(
                        login.service, login.username, user_id=self.scope, revive=True
                    )
