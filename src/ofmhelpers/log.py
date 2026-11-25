# Copyright (C) 2026 jpkrajewski

import json
import logging
import logging.config
from pathlib import Path

_configured = False


def configure_logging(*, force: bool = False) -> None:
    global _configured
    if _configured and not force:
        return
    with Path(__file__).with_name("logging.json").open(encoding="utf-8") as config_file:
        logging.config.dictConfig(json.load(config_file))
    _configured = True
