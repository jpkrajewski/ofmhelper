# Copyright (C) 2026 jpkrajewski

import base64
import hashlib
import hmac
import time
lazy from collections.abc import Mapping

from pydantic import Field, ValidationError

from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers.http import Lenient
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


class KieCallbackTask(Lenient):
    task_id: str = Field(alias="taskId", min_length=1, max_length=128)


class KieCallback(Lenient):
    data: KieCallbackTask


def _signed(task_id: str, headers: Mapping[str, str], key: str) -> bool:
    stamp = headers.get("x-webhook-timestamp", "")
    signature = headers.get("x-webhook-signature", "")
    if not stamp.isdigit() or not signature:
        logger.warning(
            "kie.ai callback for %s unsigned (timestamp %s, signature %s)",
            task_id,
            "set" if stamp else "missing",
            "set" if signature else "missing",
        )
        return False
    age_s = time.time() - int(stamp)
    if abs(age_s) > settings.kieai.callback_tolerance_s:
        logger.warning("kie.ai callback for %s signed %.0f s ago", task_id, age_s)
        return False
    expected = base64.b64encode(
        hmac.new(key.encode(), f"{task_id}.{stamp}".encode(), hashlib.sha256).digest(),
    ).decode()
    if not hmac.compare_digest(expected, signature):
        logger.warning("kie.ai callback for %s: signature does not match", task_id)
        return False
    return True


def task_id(raw: bytes, headers: Mapping[str, str]) -> str | None:
    try:
        callback = KieCallback.model_validate_json(raw)
    except ValidationError:
        logger.warning("kie.ai callback not understood: refused", exc_info=True)
        return None
    named = callback.data.task_id
    key = settings.kieai.webhook_hmac_key
    if key is not None and not _signed(named, headers, key.get_secret_value()):
        return None
    return named
