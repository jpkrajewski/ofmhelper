# Copyright (C) 2026 jpkrajewski

from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.db.devices import store as device_store
from ofmhelpers.db.phone_revisions import store
from ofmhelpers.features.accounts.tracker import track_login
from ofmhelpers.features.refusals import NotFoundError
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.phone_revisions.schema import PhoneRevision, Restored

logger = get_logger(__name__)


class Revisions(BaseModel, frozen=True):
    scope: int

    def listed(self) -> list[PhoneRevision]:
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
        if not back:
            return
        for device in device_store.list_devices(self.scope):
            for login in device.logins:
                if login.id in back:
                    track_login(
                        login.service,
                        login.username,
                        user_id=self.scope,
                        revive=True,
                    )
