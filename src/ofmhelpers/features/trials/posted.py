# Copyright (C) 2026 jpkrajewski

import logging
import time

from ofmhelpers.cache import JobQueue, QueueName
from ofmhelpers.features.storage.client import saved_storage
from ofmhelpers.features.trials import cleanup, folder
from ofmhelpers.features.trials.state import stored_folders

logger = logging.getLogger(__name__)


def read_all() -> None:
    previews = JobQueue(name=QueueName.PREVIEWS)
    for uid, login_id in stored_folders():
        try:
            if not folder.read_posted(saved_storage(uid).folders, uid, login_id):
                logger.info("trials: POSTED of %s/%s not read, no clean", uid, login_id)
                continue
            previews.put_once(
                f"trials-clean-{uid}-{login_id}",
                cleanup.clean,
                (uid, login_id, time.time()),
            )
        except Exception:
            logger.warning(
                "trials: cleanup of %s/%s failed",
                uid,
                login_id,
                exc_info=True,
            )
