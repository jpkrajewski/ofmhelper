# Copyright (C) 2026 jpkrajewski

import logging

lazy from pydantic import BaseModel

from ofmhelpers.utils.secrets.schemas import Revealed

logger = logging.getLogger(__name__)


def model_json_dump(model: BaseModel, reason: str) -> str:
    dumped = model.model_dump_json(context=Revealed(reason=reason))
    logger.info("secrets dumped as JSON: %s", reason)
    return dumped
